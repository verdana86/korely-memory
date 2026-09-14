# Korely for n8n

Bi-temporal memory in a workflow: store what happened, ask what was true on a
date, and retract a fact when it stops being true.

```
npm install n8n-nodes-korely
```

Or, in n8n: **Settings → Community Nodes → Install**, and type
`n8n-nodes-korely`.

## Why this and not the HTTP Request node

The HTTP Request node works, and it hides the one thing that goes wrong. Korely
is a hosted service *and* a thing you install, and a client with no address
picks the hosted one: a self-hosted key then travels to somebody else's server
before being refused. It is refused correctly, and it is refused too late.

So the credential asks for the address next to the key, and **Test** asks the
server whether the pair makes sense. A key that begins `kor_self_` pointed at
the hosted service fails the test with a message that says so, instead of a
blank 401 during your first run at two in the morning.

## What it does

| Resource | Operation | What it is for |
|---|---|---|
| Memory | Add | Store text; typed facts are mined from it |
| Memory | Search | Find memories by meaning |
| Context | Get | A prompt-ready block of facts and memories |
| Fact | List | What is true now, or **what was true on a date** |
| Fact | Write | Assert a triple directly, with no model involved |
| Fact | Close | It stops being current and stays in history |
| Fact | Correct | Supersede it; both stay readable |

**Close** takes the date it *stopped being true*, not the date you noticed.
Reading `As Of` a date before that still returns the fact, which is the reason
history is kept instead of rows deleted.

**Write** plus **Close** is the configuration with no model in the write path
at all: nothing has to infer a contradiction, because you said so.

## Two things worth knowing

**End User ID scopes everything.** Leaving it empty files the row under nobody,
and reads scoped to a user will not find it. It is the commonest way to end up
with a store that looks empty.

**True From matters when you backfill.** A fact written without it starts today,
and a later Close on an older date is then refused, because a fact cannot stop
being true before it began.

## No runtime dependencies

On purpose: n8n does not allow them in a verified community node. The node
calls the REST API through n8n's own request helper.

## Testing it

`test/run.mjs` drives the node against a real install with a minimal fake n8n
around it, and the requests go out over the network. A node that compiles is
not a node that works: what breaks is paths, field names and the HTTP verb, and
the compiler sees none of those.

```bash
npm run build
KORELY_BASE_URL=https://your-install KORELY_API_KEY=kor_self_... node --test test/run.mjs
```

It found a defect in the REST API on its first run, which is the argument for
writing it this way.

MIT.
