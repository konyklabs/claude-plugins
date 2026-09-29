# Modules, resolution, and exports reference

Fetched 2026-09-29 from typescriptlang.org's tsconfig reference pages and
handbook, the TypeScript 5.0 release notes, and nodejs.org's packages
documentation.

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
v12+ supports both ECMAScript imports and CommonJS require, which resolve
using different algorithms". `bundler` supports the `imports`/`exports`
fields the way `nodenext` does but never requires a file extension on a
relative import, because a bundler resolves those itself. `node10`
(formerly plain `node`): "You probably won't need to use node10 in modern
code". `classic`: "should not be used" —
https://www.typescriptlang.org/tsconfig/. The default `moduleResolution`
follows `module`: `Node10` when `module: CommonJS`; `Node16` when `module`
is `Node16`/`Node18`/`Node20`; `NodeNext` when `module: NodeNext`;
`Bundler` when `module: Preserve`.

## 2. moduleDetection

`auto` (the default) checks for an `import`/`export` statement, treats a
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
docs do not state that this is the recommended default for new projects
(NOT FOUND), though `tsc --init`-generated configs and this skill's own
sample enable it —
https://www.typescriptlang.org/tsconfig/esModuleInterop.html.

## 4. Node conditional exports

Condition order in `package.json` `exports` matters: conditions are
matched most specific to least specific. The community `"types"` condition
"should always be included first" in a conditions block so a type checker
picks it up before a runtime-only condition. The finer detail of the dual
CJS/ESM hazard (a package resolving to two different module instances) is
deferred by Node's own docs to `nodejs/package-examples` and was not
pulled into this fetch —
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
exactly as written except one case: `import type` (and a type-only named
import) is always erased. This is also the release that started
deprecating `importsNotUsedAsValues` and `preserveValueImports` in its
favor —
https://www.typescriptlang.org/docs/handbook/release-notes/typescript-5-0.html.

## 7. Traps

1. `paths` alone does nothing at runtime — pair it with a resolver, or
   drop it (section 5).
2. `verbatimModuleSyntax` erases `import type` completely: an import kept
   only for a side effect vanishes if it is written as a type import
   (section 6).
3. `esModuleInterop` left off plus a CommonJS default export: `import * as
   x from "cjs-lib"` compiles, then calling `x()` throws at runtime because
   a namespace object is not callable (section 3).
4. Trusting the `"types"` condition's position in `exports` without
   checking it is listed first — a checker can resolve the wrong file
   (section 4).
5. TypeScript 6.0 removed `node10`/`classic` resolution and the `module:
   amd|umd|systemjs|none` values a `moduleResolution` copied from an older
   project may still name (see `tsconfig.md`, section 1).
