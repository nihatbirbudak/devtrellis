# Security reporting

Please do not post a working exploit, key, personal database or private URL in a public issue. Until a dedicated security contact is published, open a GitHub issue containing only a brief request for a private contact channel and the affected version. The maintainer will provide a private route before technical details are shared.

DevTrellis has a personal single-user workspace and a separate community service. Keep the personal server bound to loopback. The community service requires HTTPS through a correctly configured same-host proxy, a matching public URL and one application worker; see COMMUNITY.md. Its HTTP pilot flag is only for loopback testing.

Published community records are untrusted text. The MCP bridge keeps every contributor-written value as a JSON string inside random `BEGIN/END UNTRUSTED` markers, writes outside them only values whose format it checked, and tells the agent not to run, install or follow anything from a record without asking the user (see MCP.md, "Record text is data"). This is a mitigation, not a lock; the server setting `hold_instruction_like` (off by default) can also hold instruction-like submissions for a moderator (docs/community-k1.md). Reports of a record text that an agent followed are welcome.

Reports about source fetching, private draft exposure, account ownership, publication revisions, provider credentials and browser requests are especially useful. Include the affected version and minimal synthetic steps. Actual secrets or other people's records are not needed.
