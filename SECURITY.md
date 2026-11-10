# Security

- No credentials, API keys, or customer data are stored in this repository. Real shop pricing, customer jobs, and machine programs belong in the git-ignored `shop/` folder or in the database, never in commits.
- The Supabase database is locked with row level security; only the server-side service role can read or write.
- Machine output is never sent to a machine directly. Every nest program must be simulated in woodWOP and approved by a person before it runs.

Report a vulnerability privately through GitHub's "Report a vulnerability" button on this repository, not in a public issue.
