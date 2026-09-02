---
type: regex
target: last_message
pattern: "join_transaction_mode\\s*=\\s*[\"']create_savepoint[\"']"
---
The answer must use the official external-transaction recipe: a Session bound to a connection with join_transaction_mode="create_savepoint" and a rollback of the outer transaction at teardown.
