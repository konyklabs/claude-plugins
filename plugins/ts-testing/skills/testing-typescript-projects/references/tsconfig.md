# tsconfig option and version reference

Fetched 2026-09-29 from typescriptlang.org's tsconfig reference and
handbook, the TypeScript blog's 5.8/5.9/6.0/7.0 announcement posts, the npm
registry, and nodejs.org.

## Contents

1. Versions: what changed under an existing config
2. Strictness flags
3. Project references and composite
4. declaration, incremental, include/exclude
5. noEmit and the surrounding tooling
6. The runtime: what Node strips and refuses
7. Traps, in the order they are found late

## 1. Versions: what changed under an existing config

npm's current `typescript` release is 7.0.2 —
https://registry.npmjs.org/typescript/latest. TypeScript 7.0 (announced
2026-07-08) is a native Go port, 7.7–11.9x faster to build and 6–26%
lighter on memory; there is no stable programmatic API until 7.1, so tools
that embed the compiler (Volar/Vue, Angular, Svelte, MDX, Astro editor
checking) stay on 6.0 for now; it drops `target: es5`,
`downlevelIteration`, and the legacy `moduleResolution` modes (summarized)
— https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/.

TypeScript 6.0 (2026-03-23), the last release on the JavaScript codebase,
changed the defaults under every config that relied on them: `strict:
true`, `module: esnext`, `target: es2025`, `noUncheckedSideEffectImports:
true`, `libReplacement: false`; `rootDir` now defaults to the tsconfig's
own directory; `types` now defaults to `[]`; `baseUrl` is deprecated;
`moduleResolution node10`/`classic` are removed or deprecated; `module
amd|umd|systemjs|none` is removed (summarized; the `baseUrl` deprecation is
cross-confirmed by the tsconfig reference page) —
https://devblogs.microsoft.com/typescript/announcing-typescript-6-0/.

TypeScript 5.9 (2025-08-01) made `tsc --init` emit a minimal config
(`module: nodenext`, `target: esnext`, `strict`, `noUncheckedIndexedAccess`,
`types: []`, `moduleDetection: force`) and stabilized `--module node20`
(implies `target es2023`; will not pick up future nodenext changes) —
https://devblogs.microsoft.com/typescript/announcing-typescript-5-9/.

TypeScript 5.8 added `--erasableSyntaxOnly` (errors on enum declarations,
runtime namespaces, parameter properties, `import =` aliases) and
`--rewriteRelativeImportExtensions` (rewrites relative `.ts`/`.tsx`/`.mts`/
`.cts` specifiers to their `.js`/`.jsx`/`.mjs`/`.cjs` output form); exact
release date NOT FOUND —
https://devblogs.microsoft.com/typescript/announcing-typescript-5-8/.
`isolatedDeclarations` exists since 5.5; `verbatimModuleSyntax` since 5.0,
deprecating `importsNotUsedAsValues`/`preserveValueImports` —
https://www.typescriptlang.org/docs/handbook/release-notes/typescript-5-0.html.
Native-compiler project: https://github.com/microsoft/typescript-go.

## 2. Strictness flags

All from https://www.typescriptlang.org/tsconfig/: `noUncheckedIndexedAccess`
(4.1) adds `undefined` to every index-signature read (`arr[i]`, `map[key]`)
— the flag that touches the most lines and finds the most real bugs.
`exactOptionalPropertyTypes` (4.4) is marked Recommended.
`noImplicitOverride` (4.3) and `noPropertyAccessFromIndexSignature` (4.2)
are cheap, few-line additions, as is `noFallthroughCasesInSwitch` (1.8).
`useUnknownInCatchVariables` (4.4, Recommended) is on by default under
`strict`, itself the default since 6.0 — a caught error is `unknown`, not
`any`. No official adoption order for an existing codebase (NOT FOUND);
one flag per PR, cheapest first, is a practice, not a documented rule.
`JSON.parse` returns `any` regardless, and the maintainers' issue for a
safer type stays open — https://github.com/microsoft/TypeScript/issues/26993
— so the boundary pattern is to cast to `unknown` and validate.

## 3. Project references and composite

`composite: true` forces `declaration: true`, defaults `rootDir` to the
tsconfig's own directory, and requires every implementation file reached by
the project to be matched by `include`/`files` — a file it misses is
silently invisible to any project that references this one. The solution
pattern is a root `tsconfig.json` with `"files": []` and a `"references"`
array pointing at each package; `tsc -b` builds in dependency order and
takes `--verbose`, `--dry`, `--clean`, `--force`, `--watch`. The docs do not
mention a `tsconfig.build.json`-style split; that is a community
convention, not a compiler feature —
https://www.typescriptlang.org/docs/handbook/project-references.html.

## 4. declaration, incremental, include/exclude

`declaration` defaults to `true` if `composite`, else `false`; the docs say
to "strongly consider turning this on if you're using project references"
for `declarationMap`, so go-to-definition lands in the dependency's source
rather than its `.d.ts`. `incremental` pairs with `tsBuildInfoFile` to
avoid a full re-check; `skipLibCheck`'s exact reference wording was not
confirmed this fetch (NOT FOUND). On `include`/`exclude`: "exclude only
changes which files are included as a result of the include setting" — a
file reached through an import, a `types` entry, or a triple-slash
reference is still in the program no matter what `exclude` says —
https://www.typescriptlang.org/tsconfig/.

## 5. noEmit and the surrounding tooling

`tsc --noEmit`: "Do not emit compiler output files like JavaScript source
code, source-maps or declarations" —
https://www.typescriptlang.org/tsconfig/noEmit.html. This is the check
that catches test code drifting from the real types; a Vitest run
type-checks nothing on its own, because esbuild strips types without
checking them, so a green suite can sit on a broken type.
`typescript-eslint`'s `parserOptions.projectService` (stable since 8.0)
gives type-aware lint rules with no separate lint-only tsconfig, and
supports project references; since 8.33.0 it is extracted into
`@typescript-eslint/project-service` —
https://typescript-eslint.io/blog/project-service/,
https://typescript-eslint.io/getting-started/typed-linting/. `publint`
checks a package's `exports`/`main`/`module`/`types`/`bin` fields against
what is actually on disk (its exact CLI invocation was NOT FOUND this
fetch) — https://publint.dev/. `arethetypeswrong` analyses a published
tarball's type resolution across node10, node16 and bundler resolution —
https://github.com/arethetypeswrong/arethetypeswrong.github.io. `knip`
finds unused files, dependencies and exports —
https://knip.dev/. `isolatedDeclarations` (5.5) makes declaration emit a
per-file job and rejects an exported value whose type can only be
inferred, not written down —
https://www.typescriptlang.org/tsconfig/isolatedDeclarations.html.

## 6. The runtime: what Node strips and refuses

Per nodejs.org/api/typescript.html (summarized, moderate confidence): type
stripping was behind `--experimental-strip-types` from Node 22.6.0, added
`--experimental-transform-types` at 22.7.0, on by default at
22.18.0/23.6.0, stable from 24.12.0/25.2.0; Node 26.0.0+ has it on by
default with the transform flag removed. Node 24.0.0's release notes
labelled it a Release Candidate — https://nodejs.org/en/blog/release/v24.0.0.
Stripping is erasure only: an `enum` declaration, a `namespace` carrying
runtime code, a parameter property, or an `import =` alias throws
`ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX`; a type-only namespace is fine —
https://nodejs.org/api/typescript.html. As of 2026-09-29, Node 26 is
Current, Node 24 "Krypton" is Active LTS (since 2025-10-28), Node 22 "Jod"
is in Maintenance (summarized) —
https://raw.githubusercontent.com/nodejs/Release/main/README.md. `tsx`
(npm latest 4.23.15) runs TypeScript/ESM through esbuild —
https://registry.npmjs.org/tsx/latest; its transpile-only behavior is
blog-only, unverified here. `ts-node` (npm latest 10.9.2, last-publish
date NOT FOUND) offers `--transpileOnly`/`--typeCheck` and is for existing
setups, not new ones — https://registry.npmjs.org/ts-node/latest,
https://github.com/TypeStrong/ts-node. Whether `@types/node`'s major
tracks the Node major is unconfirmed (npm page returned 403 this fetch).

## 7. Traps, in the order they are found late

1. An `enum`, a runtime `namespace`, a parameter property, or `import =`
   under Node's type stripping throws at runtime (section 6).
2. A `composite` project whose `include` misses a file: no error at build
   time, just types silently missing downstream (section 3).
3. `erasableSyntaxOnly` only protects a build that turns it on (section 1).
4. `isolatedDeclarations` rejects an exported value whose type is
   inference-only, not written down (section 5).
5. A TypeScript 6 upgrade turns on `strict` and `types: []`, makes
   `baseUrl` a warning, and removes `node10`/`classic` resolution and
   `module: amd|umd|systemjs` — a config that relied on any of the old
   defaults now behaves differently with no code change (section 1).
6. `declaration` emit can fail on an inferred export type that names a
   type which is not itself exported — export the type, or annotate the
   value explicitly.
