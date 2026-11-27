# Portal applications

- Farbod logs in himself; the agent fills the remaining pages and presses Save, never Submit.
- Never pay fees; never type passport or ID numbers.
- Check institutions.yaml portal notes before each page (e.g. Oxford sends reference requests the
  moment you leave the References tab with "ready" ticked).
- Referees: Mohammadinia (academic 1), Jarrahi, Gholami. Ask Farbod before triggering any
  reference request.
- Equality and socio-economic questions: "Prefer not to say".
- Course found via: "FindAPhD website" when that is true.
- When done, tell Farbod exactly what is left: login, declaration, payment, Submit.

## Portal packs (phase 8)

- The agent builds a pack of facts only (portal_pack.json and Farbod_Fooladi_Portal_Pack.md): no
  model calls, no documents, no private data.
- Documents and form filling happen in the Portal session of the Find PHD project (skill
  portal-apply), always on the university's own template when one exists.
- Programme caps live in knowledge/programmes.yaml; when a submission fills a cap the other open
  positions of that programme are closed.
- Farbod presses Submit himself, then sends /done <id> <reference> in Telegram (or phd portal-done).
  Deadline nudges at 7, 3, 1 and 0 days.
