# tsconfig option and version reference

Fetched 2026-10-29 from typescriptlang.org's tsconfig reference and
handbook, the TypeScript blog's 5.7/5.8/5.9/6.0/7.0/7.1 announcement posts,
the npm registry, and nodejs.org.

## Contents

1. Versions: what changed under an existing config
2. Strictness flags
3. Project references and composite
4. declaration, incremental, include/exclude
5. noEmit and the surrounding tooling
6. The runtime: what Node strips and refuses
7. Traps, in the order they are found late

## 1. Versions: what changed under an existing config

npm's `latest` `typescript` is 7.0.2 (published 2026-07-08) —
https://registry.npmjs.org/typescript/latest. TypeScript 7.0 (announced
2026-07-08) is a native Go port, 7.7–11.9x faster on full builds and 6–26%
lighter on memory; it ships no programmatic API, so tools that embed the
compiler (Volar/Vue, Angular templates, Svelte, MDX, Astro editor checking,
typescript-eslint) stay on 6.0, and the `@typescript/typescript6` package
re-exports the 6.0 API for them. The deprecated options are now hard errors
that do nothing: `target: es5`, `downlevelIteration`, `moduleResolution
node`/`node10`/`classic`, `module amd|umd|systemjs|none`, `baseUrl`, and
`esModuleInterop`/`allowSyntheticDefaultImports`/`alwaysStrict` set to
`false`; a file path on the command line next to a `tsconfig.json` is also
an error unless `--ignoreConfig` is passed —
https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/.
TypeScript 7.1 is in beta (announced 2026-10-20; npm `beta` tag
7.1.0-beta; release candidate planned about a month later) and is the first
to ship an API (`typescript/sync`, `typescript/async`, `typescript/ast`,
`typescript/fs`); the post gives no stability guarantee, and the API does
not load `tsconfig.json` or take `--project`; it also stabilizes `--target
es2026` and `--lib es2026`, with no default changes —
https://devblogs.microsoft.com/typescript/announcing-typescript-7-1-beta/.
The native-compiler repo microsoft/typescript-go is archived (read-only
since 2026-09-01), development having moved to microsoft/TypeScript —
https://github.com/microsoft/typescript-go.

TypeScript 6.0 (2026-03-23), the last release on the JavaScript codebase,
changed the defaults under every config that relied on them: `strict:
true`, `module: esnext`, `target: es2025`, `noUncheckedSideEffectImports:
true`, `libReplacement: false`; `rootDir` now defaults to the tsconfig's
own directory; `types` now defaults to `[]` (`"*"` restores the 5.9
behavior); `baseUrl` is deprecated and no longer a module lookup root (move
the prefix into `paths`); `moduleResolution node10` is deprecated and
`classic` removed; `module amd|umd|systemjs|none` and `outFile` are removed;
`target: es5` is deprecated (lowest is ES2015); `ignoreDeprecations: "6.0"`
silences the deprecations until 7.0, which supports none of them. The
tsconfig reference page itself only says `baseUrl` is "not recommended" outside
AMD loaders, so the deprecation rests on the blog post —
https://devblogs.microsoft.com/typescript/announcing-typescript-6-0/.

TypeScript 5.9 (2025-08-01) made `tsc --init` emit a minimal config
(`module: nodenext`, `target: esnext`, `strict`, `noUncheckedIndexedAccess`,
`exactOptionalPropertyTypes`, `verbatimModuleSyntax`, `isolatedModules`,
`noUncheckedSideEffectImports`, `skipLibCheck`, `types: []`,
`moduleDetection: force`) and stabilized `--module node20` (implies `target
es2023`; will not pick up future nodenext changes, whereas nodenext implies
the floating `esnext` target) —
https://devblogs.microsoft.com/typescript/announcing-typescript-5-9/.

TypeScript 5.8 (2025-02-28) added `--erasableSyntaxOnly` (errors on enum
declarations, runtime namespaces, parameter properties, `import =` aliases;
the post advises pairing it with `verbatimModuleSyntax`) —
https://devblogs.microsoft.com/typescript/announcing-typescript-5-8/.
`--rewriteRelativeImportExtensions` is older, from 5.7 (2024-11-22): it
rewrites relative `.ts`/`.tsx`/`.mts`/`.cts` specifiers to their `.js`/`.jsx`/
`.mjs`/`.cjs` output form, and leaves non-relative or already-`.js` paths
alone — https://devblogs.microsoft.com/typescript/announcing-typescript-5-7/.
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
`any`. `skipLibCheck` (Recommended, 2.0) skips type checking of declaration
files, trading type-system accuracy for time (two libraries may define the
same type inconsistently). No official adoption order for an existing codebase (NOT FOUND);
one flag per PR, cheapest first, is a practice, not a documented rule.
`JSON.parse` returns `any` regardless: the maintainers' issue for a safer
type was closed "Working as Intended" in 2018 and is locked —
https://github.com/microsoft/TypeScript/issues/26993 — so the boundary
pattern is to cast to `unknown` and validate.

## 3. Project references and composite

`composite: true` defaults `declaration` to `true` and `rootDir` to the
tsconfig's own directory, and requires every implementation file reached by
the project to be matched by `include`/`files`; per the docs `tsc` then
names the files that were not specified, so a miss is an error to read, not
a silent one. Referenced projects must set `composite`. The solution
pattern is a root `tsconfig.json` with `"files": []` and a `"references"`
array pointing at each leaf project (otherwise the solution file compiles
files twice); `tsc -b` builds in dependency order, behaves as if
`noEmitOnError` were on for every project, and takes `--verbose`, `--dry`,
`--clean`, `--force`, `--watch`; plain `tsc` never builds dependencies.
Dependents read the `.d.ts` their dependencies built, so a fresh clone shows
editor errors until a `tsc -b` has run. The docs do not
mention a `tsconfig.build.json`-style split; that is a community
convention, not a compiler feature —
https://www.typescriptlang.org/docs/handbook/project-references.html.

## 4. declaration, incremental, include/exclude

`declaration` defaults to `true` if `composite`, else `false`; the
`declarationMap` page says "You should strongly consider turning this on if
you're using project references", so go-to-definition lands in the
dependency's source rather than its `.d.ts`. `incremental` defaults to
`true` if `composite`, else `false`, and writes `.tsbuildinfo` files that are
safe to delete; `tsBuildInfoFile` sets where. On `include`/`exclude`: "exclude only
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
supports project references (the blog says most projects can drop their
`tsconfig.eslint.json`; `allowDefaultProject` lints files outside the main
tsconfig); since 8.33.0 it is extracted into
`@typescript-eslint/project-service`. It needs programmatic compiler access,
so under TypeScript 7 it runs on the 6.0 compatibility package —
https://typescript-eslint.io/blog/project-service/,
https://typescript-eslint.io/getting-started/typed-linting/ (its flat-config
example is `projectService: true`). `publint` lints a package for
compatibility across Vite, Webpack, Rollup and Node.js; run it as `npx
publint` (or `publint ./dir`) after the build step, so it checks the files
that will be published — https://publint.dev/docs/. `arethetypeswrong`
analyses a package's type resolution across node10, node16 and bundler
resolution; `npx --yes @arethetypeswrong/cli --pack .` packs and checks in
place, and `--profile strict|node16|esm-only` chooses which failures count —
https://github.com/arethetypeswrong/arethetypeswrong.github.io. `knip` finds
unused files, dependencies and exports (`npx knip`) —
https://knip.dev/. `isolatedDeclarations` (5.5) makes declaration emit a
per-file job and rejects an exported value whose type can only be
inferred, not written down —
https://www.typescriptlang.org/tsconfig/isolatedDeclarations.html.

## 6. The runtime: what Node strips and refuses

Per nodejs.org/api/typescript.html (history table; the page now shows
"Stability: 2 - Stable"): type stripping arrived in Node 22.6.0, with
`--experimental-transform-types` added at 22.7.0; it is on by default from
22.18.0/23.6.0, emits no experimental warning from 22.18.0/24.3.0, is marked
stable from 24.12.0/25.2.0, and Node 26.0.0 removed the transform flag.
(The page does not mention `--experimental-strip-types`; it documents
`--no-strip-types` to turn stripping off.) Node 24.0.0's release notes
labelled it a Release Candidate — https://nodejs.org/en/blog/release/v24.0.0.
Stripping is erasure only: an `enum` declaration, a `namespace` carrying
runtime code, a parameter property, or an `import =` alias throws
`ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX`, decorators are a parser error, and a
type-only namespace is fine. Node ignores `tsconfig.json`, so `paths` and
down-leveling are intentionally unsupported (a `paths` alias is an error),
and file extensions are mandatory in `import` specifiers. The page's
recommended config (TypeScript 5.8+) is `target: esnext`, `module:
nodenext`, `rewriteRelativeImportExtensions`, `erasableSyntaxOnly` and
`verbatimModuleSyntax`, with `noEmit` only when `*.ts` is run directly —
https://nodejs.org/api/typescript.html. As of 2026-10-29, by the release
schedule's dates: Node 26 entered Active LTS on 2026-10-28 (the dist index
names it "Lithium"; the README's status column still said "Current" and
gave it no codename), Node 24 "Krypton" went to Maintenance on 2026-10-20,
Node 22 "Jod" is in Maintenance since 2025-10-21 — a rolling fact, so
re-check it —
https://raw.githubusercontent.com/nodejs/Release/main/README.md,
https://nodejs.org/dist/index.json. `tsx` (npm latest 4.23.15, published
2026-09-20) runs TypeScript/ESM through esbuild —
https://registry.npmjs.org/tsx/latest; that it does not type-check is
blog-only, unverified here (its README says only "The easiest way to run
TypeScript in Node.js"). `ts-node` (npm latest 10.9.2, published
2023-12-08; a `11.0.0-beta.1` sits on the `beta` tag) offers
`--transpileOnly` (default off) and `--typeCheck` (default on) and is for
existing setups, not new ones — https://registry.npmjs.org/ts-node,
https://github.com/TypeStrong/ts-node. `@types/node`'s major does track the
Node major: its npm `latest` is 26.6.6 and majors 24 and 25 exist
(registry, 2026-10-29) — https://registry.npmjs.org/@types/node.

## 7. Traps, in the order they are found late

1. An `enum`, a runtime `namespace`, a parameter property, or `import =`
   under Node's type stripping throws at runtime (section 6).
2. A `composite` project whose `include` misses a file: `tsc` names the
   unspecified files, but a dependent project built from stale `.d.ts`
   outputs sees missing types until `tsc -b` reruns (section 3).
3. `erasableSyntaxOnly` only protects a build that turns it on (section 1).
4. `isolatedDeclarations` rejects an exported value whose type is
   inference-only, not written down (section 5).
5. A TypeScript 6 upgrade turns on `strict` and `types: []`, deprecates
   `baseUrl` and `node10` resolution, and removes `classic` resolution and
   `module: amd|umd|systemjs` — a config that relied on any of the old
   defaults now behaves differently with no code change, and 7.0 turns every
   one of those deprecations into a hard error (section 1).
6. `declaration` emit can fail on an inferred export type that names a
   type which is not itself exported — export the type, or annotate the
   value explicitly.
