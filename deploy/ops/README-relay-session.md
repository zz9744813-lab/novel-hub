# Relay conversation header

The OpenCode Go operator [announced a session-header requirement effective September 5, 2026](https://github.com/vercel/ai/issues/20271).
It is routing metadata, not an authentication credential or permission bypass.

NovelForge supplies its own UUID in `x-opencode-session` for New API/primary
and OpenCode routes. Each logical generation has its own ID; transport retries
reuse it. Each health probe uses its probe UUID, and ability-case retries share
one generated UUID. Callers can pass `conversation_id` to retain the same ID
across a longer conversation. No shared global ID, content-derived ID, fake
OpenCode client header, user identity, or credential is used.

## New API channel configuration

A client header does not imply forwarding to the upstream. On September 7,
the current gateway returned MissingSessionID even when the actual outgoing
client request contained the header. The application reports this as
`UPSTREAM_SESSION_REQUIRED` and does not retry it as a missing model.

An administrator must inspect the **actual channel serving this model** and,
if supported by that installed New API version, merge this entry into the
channel's **Header Override** configuration, preserving every existing entry:

```json
{
  "X-Opencode-Session": "{client_header:x-opencode-session}"
}
```

This syntax is implemented in the [New API channel request source](https://github.com/QuantumNous/new-api/blob/main/relay/channel/api_request.go).
It is not the request-body Parameter Override field. Do not set a fixed ID for
all clients, forward all headers with `*`, replace Authorization, change
upstream credentials, or change unrelated channels. Other clients of the same
channel must also send their own conversation ID; do not invent one to bypass
an upstream authorization, region, billing, or training-consent restriction.

Current upstream New API code skips client-header placeholders for its
internal channel-test endpoint. Therefore the console's Test button is not
sufficient evidence: verify with an actual authenticated inference request
carrying a real application conversation ID.

## Release checks

1. One lightweight non-streaming request through the configured gateway.
2. One bounded structured streaming request with the normal runtime settings.
3. An isolated candidate with the production credential, capability evidence
   and migration gates. A local Hermes key is not proof of this step.
4. Only a passing candidate may proceed to backup/deploy and chapter canary.

Neither this document nor the client implementation modifies the gateway's
administrator configuration automatically.
