---
type: regex
target: last_message
pattern: "DEPLOY_TOKEN:\\s*\\$\\{\\{\\s*secrets\\.DEPLOY_TOKEN|secrets:\\s*inherit"
---
The secret must cross the call boundary explicitly: either named
(`DEPLOY_TOKEN: ${{ secrets.DEPLOY_TOKEN }}`) or via `secrets: inherit`,
which is only valid here because the callee (`konyklabs/.github`) is in
the same org as the caller. A caller that omits `secrets:` entirely
leaves the callee's required secret unset.
