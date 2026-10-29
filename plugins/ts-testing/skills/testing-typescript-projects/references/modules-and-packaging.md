# Modules, resolution, and exports reference

Fetched 2026-10-29 from typescriptlang.org's tsconfig reference pages and
handbook, the TypeScript 5.0 release notes, the TypeScript 6.0 announcement
post, and nodejs.org's packages documentation.

## Contents

1. moduleResolution: the four modes and their default
2. moduleDetection
3. esModuleInterop
4. Node conditional exports
5. paths is a type-checker fact, not a runtime one
6. verbatimModuleSyntax
7. Traps

## 1. moduleResolution: the four modes and their default

`nodenext`/`node16` integrate with Node's native ESM resolution: "Node.js
v12 and later supports both ECMAScript imports and CommonJS `require`, which
resolve using different algorithms", and the mode picks the algorithm per
import by what Node will see in the output. `bundler` supports the
`imports`/`exports` fields the way `nodenext` does but never requires a file
extension on a relative import, because a bundler resolves those itself; it
suits an application behind Vite, esbuild, webpack and the like, but for a
library published to npm "using the `bundler` option can hide compatibility
issues" for consumers who use no bundler, so prefer `node16`/`nodenext`
there (5.0 release notes). `node10` (formerly plain `node`): "You probably
won't need to use node10 in modern code". `classic`: "should not be used" —
https://www.typescriptlang.org/tsconfig/. The default `moduleResolution`
follows `module`: `Node10` when `module: CommonJS`; `Node16` when `module`
is `Node16`/`Node18`/`Node20`; `NodeNext` when `module: NodeNext`;
`Bundler` when `module: Preserve`; `Classic` otherwise. TypeScript 6.0 then
changed the status of the old modes (trap 5).

## 2. moduleDetection

`auto` (the default) checks for an `import`/`export` statement (or
`import.meta`), treats a
file as a module under `nodenext`/`node16` when the nearest `package.json`
says `"type": "module"`, and treats JSX as a module signal under `jsx:
react-jsx`. `legacy` is the pre-`auto` behavior. `force` treats every
non-declaration file in the program as a module regardless of content —
https://www.typescriptlang.org/tsconfig/moduleDetection.html.

## 3. esModuleInterop

Without it, `import * as x from "cjs-lib"` behaves like a plain `require()`
call and can be invoked as a function even though ES6 requires a namespace
import to be a plain object — a shape mismatch that only shows up at
runtime. Turning it on also turns on `allowSyntheticDefaultImports`. The
reference page labels it "Recommended" and defaults it to `true` when
`module` is `node16`, `nodenext` or `preserve`, `false` otherwise. Its
namespace import only sees own properties, so a module that defines its API
through inherited ones needs the default import form. In TypeScript 6.0 it
(and `allowSyntheticDefaultImports`) can no longer be set to `false` —
https://www.typescriptlang.org/tsconfig/esModuleInterop.html and
https://devblogs.microsoft.com/typescript/announcing-typescript-6-0/.

## 4. Node conditional exports

Key order in the `exports` object is significant: earlier entries win, so
write conditions "from most specific to least specific". `"default"` is the
generic fallback and "should always come last". `"import"` and `"require"`
are always mutually exclusive; `"module-sync"` (an ES module with no
top-level await) matches under both. The community `"types"` condition,
documented in Node's separate community-conditions section, "should always
be included first" so a type checker picks it up before a runtime-only
condition. The finer detail of the dual CJS/ESM hazard (a package resolving
to two different module instances) is deferred by Node's own docs to
`nodejs/package-examples` and was not pulled into this fetch —
https://nodejs.org/api/packages.html#conditional-exports.

## 5. paths is a type-checker fact, not a runtime one

`paths` "does not change how import paths are emitted by tsc, so paths
should only be used to inform TypeScript that another tool has this
mapping and will use it at runtime or when bundling" —
https://www.typescriptlang.org/tsconfig/. In practice: an import that
resolves in the editor and throws `Cannot find module` when run is `paths`
with nothing providing that mapping at runtime — a bundler, `vite-tsconfig-
paths` in tests (see the Vitest skill's `config.md`), or Node's own
`imports` field in `package.json`, which needs no separate mapping at all.

## 6. verbatimModuleSyntax

Since TypeScript 5.0, `verbatimModuleSyntax` keeps every import and export
without a `type` modifier exactly as written and drops anything with one:
`import type { A }` vanishes, `import { b, type c }` becomes `import { b }`,
and `import { type x } from "m"` becomes `import {} from "m"` (a side-effect
import survives). Under the flag, ESM syntax is never rewritten to
`require`: a file whose settings imply CommonJS gets an error instead, and
CommonJS output needs `import x = require("x")` and `export =`. This is also
the release that deprecated `importsNotUsedAsValues` and
`preserveValueImports` in its favor —
https://www.typescriptlang.org/docs/handbook/release-notes/typescript-5-0.html.

## 7. Traps

1. `paths` alone does nothing at runtime — pair it with a resolver, or
   drop it (section 5).
2. `verbatimModuleSyntax` erases `import type` completely: an import kept
   only for a side effect vanishes if it is written as a type import
   (section 6). Under `module: commonjs` it errors on ESM syntax instead of
   rewriting it.
3. Before TypeScript 6.0, `esModuleInterop` left off plus a CommonJS default
   export: `import * as x from "cjs-lib"` compiles, then calling `x()`
   throws at runtime because a namespace object is not callable (section 3).
   From 6.0 the option cannot be `false`, so that config is an error.
4. Trusting the `"types"` condition's position in `exports` without
   checking it is listed first, or putting `"default"` before a specific
   condition — a checker can resolve the wrong file (section 4).
5. TypeScript 6.0 deprecated `node10` (silenced by `ignoreDeprecations:
   "6.0"`, unsupported in 7.0) and removed `classic`, and no longer
   supports the `module: amd|umd|systemjs|none` values; a `moduleResolution`
   copied from an older project may still name them. The tsconfig reference
   page still lists `node10` and `classic` without a removal note; the
   announcement post is the source for the change (see `tsconfig.md`,
   section 1). Also `baseUrl` is no longer a lookup root: write `./src/...`
   in each `paths` entry.
