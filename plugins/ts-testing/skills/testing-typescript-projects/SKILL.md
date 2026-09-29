---
name: testing-typescript-projects
description: How a TypeScript project is configured so its tests and builds behave — the tsconfig for Node with nodenext and .js extensions, the TypeScript 6 and 7 defaults that changed under existing configs (strict on, baseUrl deprecated, types empty, native compiler with no programmatic API yet), the strictness knobs and when to adopt them, an editor config versus a build config, project references, path aliases and the runtime resolver they need, Node's type stripping and the syntax it refuses, and tsc --noEmit as the type gate. Use when creating or restructuring a TypeScript project, when tests type-check nothing, when an import resolves in the editor and fails at runtime, when upgrading TypeScript, or when deciding how a package is published.
---

# TypeScript projects that test and build the same way

TypeScript 7.0 is current (7.0.2 on npm, announced 2026-07-08): the native
compiler, 7 to 12 times faster, with no stable programmatic API until 7.1,
so tools that embed the compiler (Vue, Angular, Svelte, Astro editor
checking) still pin 6.0. TypeScript 6.0 (2026-03-23) changed the defaults
under every config that relied on them. Both are what an upgrade PR reads
first.

## 1. Options come from the docs, not from memory

- **Context7 when the MCP server is present**: resolve the library id for
  `typescript` once, then query the option (`tsconfig moduleResolution`).
- **Otherwise** `https://www.typescriptlang.org/tsconfig/` (one page per
  option), the release notes under `/docs/handbook/release-notes/`, and
  `nodejs.org/api/typescript.html` for what the runtime strips.

`references/tsconfig.md` carries the option facts and the 5.8 to 7.0
changes; `references/modules-and-packaging.md` the module and `exports`
rules.

## 2. The config for a Node service or CLI

```jsonc
// tsconfig.json — what the editor and the tests see
{
  "compilerOptions": {
    "module": "nodenext",            // as Node resolves; package.json says "type": "module" or this is CJS
    "rewriteRelativeImportExtensions": true,   // source imports ./x.ts (Node runs it as is); tsc emits ./x.js
    "target": "es2023",              // the oldest Node you run; module node20 implies es2023
    "strict": true,                  // the default since 6.0; write it anyway
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true,
    "noImplicitOverride": true,
    "verbatimModuleSyntax": true,    // import type is erased, everything else kept as written
    "erasableSyntaxOnly": true,      // no enums, namespaces, parameter properties: Node can strip it
    "types": ["node"],               // the default is [] since 6.0: name what you need
    "declaration": true, "declarationMap": true, "sourceMap": true, "skipLibCheck": true,
    "outDir": "dist"
  },
  "include": ["src", "test"]
}
// tsconfig.build.json — what ships
{ "extends": "./tsconfig.json", "include": ["src"], "exclude": ["**/*.test.ts"] }
```

- Two configs, one extending the other: the editor and Vitest see `test/`;
  `tsc -p tsconfig.build.json` emits `src/` only. The split is a
  convention, not a compiler feature; the compiler's own answer for many
  packages is project references (section 5).
- `nodenext` reads the nearest package.json: without `"type": "module"`
  every file is CommonJS and `verbatimModuleSyntax` rejects its `import`
  and `export` lines, so a service's package.json says `"type": "module"`.
  Relative imports name a file with its extension: `./util.js` when the
  emitted JavaScript runs, `./util.ts` when Node runs the source (section
  4), and `rewriteRelativeImportExtensions` (5.8) turns the latter into the
  former on emit. `bundler` resolution needs no extensions and is for code a
  bundler consumes, not code Node runs.
- `paths` informs the type checker only; `tsc` never rewrites emitted
  specifiers. An import that resolves in the editor and throws `Cannot find
  module` at runtime is `paths` without a runtime resolver (a bundler,
  `vite-tsconfig-paths` in tests, or package.json `imports`, which needs no
  mapping). `baseUrl` is deprecated in 6.0: drop it.
- `exclude` only narrows `include`; a file reached through an import is in
  the program regardless. `composite` additionally requires every
  implementation file to be matched, or it is silently invisible to the
  projects that reference it.
- `types: []` since 6.0 means `@types/node` is unseen until named or
  imported; the first `process.env` error after an upgrade is this.

## 3. Strictness: turn on early, adopt late with care

`strict` is the default since 6.0 and includes `useUnknownInCatchVariables`.
Beyond it, the docs mark `exactOptionalPropertyTypes` as recommended;
`noUncheckedIndexedAccess` turns every `arr[i]` and `map[key]` into
`T | undefined`, which finds real bugs and touches the most lines. The docs
give no adoption order; what works is one flag per PR, `noImplicitOverride`
and `noFallthroughCasesInSwitch` first (few lines), then
`noUncheckedIndexedAccess` behind a `// @ts-expect-error` budget you burn
down. `JSON.parse` returns `any` and always will (the issue is open): cast
to `unknown` at the boundary and validate.

## 4. The runtime: what Node strips and what it refuses

Node strips types itself: behind a flag from 22.6, on by default from
22.18 and 23.6, stable from 24.12 and 25.2. Stripping is erasure only: an
`enum`, a `namespace` with runtime code, a parameter property or an
`import =` alias throws `ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX`. Set
`erasableSyntaxOnly` so `tsc` refuses that syntax before Node does. Node
also rewrites nothing else: a `.ts` file run directly imports its siblings
as `.ts` (section 2). Run through `node` on 24+, or `tsx` (4.23.x) on an
older runtime; `ts-node` (10.9.2) has not moved since 2023. Align `@types/node`
with the Node major you run. Node 24 is the active LTS on 2026-09-29; Node
26 becomes one on 2026-10-28.

## 5. Project references for anything with two packages

A root `tsconfig.json` with `"files": []` and `"references": [...]`, each
leaf `composite: true` (forces `declaration`, pins `rootDir`), `tsc -b` to
build in dependency order (`--force` when the `.tsbuildinfo` files lie),
`declarationMap` on so go-to-definition lands in source, `tsBuildInfoFile`
under `node_modules/.cache` or `dist`, never committed.

## 6. The gates

```
tsc --noEmit -p tsconfig.json          # the type gate, over src and test
tsc -p tsconfig.build.json             # what ships; fails on declaration emit problems
vitest run --typecheck                 # the *.test-d.ts files, through tsc
npx publint && npx @arethetypeswrong/cli --pack .   # exports and types resolve for node16 and bundler consumers
```

- `tsc --noEmit` on the editor config catches test code drifting from the
  types; a Vitest run type-checks nothing on its own (esbuild strips
  types), so a suite can be green with a broken type.
- `typescript-eslint` with `parserOptions.projectService` (stable since 8.0)
  gives type-aware rules without a lint-only tsconfig; Biome 2 has its own
  inference for a growing subset. `knip` before a package is published.

## 7. Traps, in the order they are found late

1. `paths` with no runtime resolver (section 2).
2. An `enum` under Node type stripping (section 4).
3. `verbatimModuleSyntax` erases `import type` entirely: an import kept for
   its side effect vanishes if written as a type import.
4. `esModuleInterop` off with a CommonJS default export: `import * as x`
   compiles, then `x()` throws because a namespace is an object. Leave it on.
5. A `composite` project whose `include` misses a file: no error, missing
   types downstream.
6. TypeScript 6 upgrade: `strict` and `types: []` now on, `baseUrl`
   deprecated, `node10` and `classic` resolution gone, `module:
   amd|umd|systemjs` gone; 7 drops `target: es5` and `downlevelIteration`.

## Sources

typescriptlang.org tsconfig reference and handbook (project references,
release notes 5.0), the TypeScript blog posts for 5.8, 5.9, 6.0 and 7.0,
the npm registry (typescript 7.0.2, tsx 4.23.15, ts-node 10.9.2),
nodejs.org (typescript, packages, the release schedule), typescript-eslint
(project service), publint.dev, the arethetypeswrong and knip
repositories, the Biome v2 and 2026 roadmap posts; fetched 2026-09-29.
Not confirmed that day and left out: the exact 5.8 release date,
`skipLibCheck`'s reference wording, publint's flags, the `@types/node`
versioning statement, tsx's transpile-only claim (blog only). Details and
quotes in `references/`.
