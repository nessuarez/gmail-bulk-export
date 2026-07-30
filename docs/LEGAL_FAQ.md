# Legal & privacy FAQ

**This is not legal advice.** Nobody involved in this project is your lawyer.
This page exists to prompt the right questions before you run this tool
against real mail, not to answer them for your specific situation. Consult
a qualified lawyer in your jurisdiction before pointing this at mailboxes
that aren't only your own.

## What this tool actually does, in plain terms

It reads the **full content** of every email — and, if you enable the
attachments phase, the attachment files themselves — in every mailbox you
point it at. It does this using admin-level impersonation
(domain-wide delegation): the mailbox owner does not see a consent prompt,
cannot decline, and has no real-time visibility into the fact that their mail
is being read and exported.

That's the single fact to internalize before running this: it is a
surveillance-capable tool by construction, even when the use case is entirely
legitimate (e.g. an organization archiving its own business correspondence).
The technical capability and the legitimacy of a given use are two separate
questions, and this page is only about helping you think through the second
one.

## Whose mail are you actually reading?

Two very different situations, often confused because the mechanism (a
Workspace admin exporting mailboxes) looks the same either way:

- **Exporting your own organization's business mailboxes**, as the Workspace
  admin, for archival, backup, or internal analytics — the most common,
  usually most defensible use. You (or your organization) are both the data
  controller and the mailbox owner in a meaningful sense.
- **Exporting mailboxes that functionally belong to other identifiable
  people** — employees using individual `firstname.lastname@` addresses,
  contractors, or anyone whose personal correspondence might be mixed into
  what you're exporting. This is where real legal obligations are most likely
  to attach, because you are now processing someone else's personal data and
  potentially their private correspondence, not just your organization's
  business records.

If you're not sure which situation you're in, assume the second and act
accordingly until you've confirmed otherwise.

## Concepts worth researching for your jurisdiction

None of the following is a legal conclusion — treat each one as a research
prompt, not an answer.

- **Employee monitoring / workplace privacy law.** This varies enormously by
  country and even by state/region. The EU and several other jurisdictions
  often require an explicit legal basis, advance notice to employees, and
  sometimes consultation with a works council or employee representatives
  before an employer can access or export employee correspondence. Many
  jurisdictions outside the EU still require advance notice even without a
  works-council requirement. "The company owns the mailbox" is not
  universally sufficient justification on its own.
- **GDPR-adjacent concepts, if anyone whose mail you're exporting is in the
  EU/EEA/UK** — regardless of where you or your servers are located: lawful
  basis for processing, data minimization, defined retention limits, and data
  subjects' rights to access or request erasure of their data. Once you run
  this tool, **the CSV, Parquet, and body files it produces are a new data
  store you are now responsible for**, separate from Gmail's own controls and
  audit trail.
- **Customer or third-party correspondence.** A mailbox you're exporting
  likely contains mail *from* customers, vendors, or other third parties who
  never agreed to have their correspondence bulk-exported and stored outside
  Gmail. Their data ends up in your export too, even though they're not the
  mailbox owner.
- **Sector-specific rules**, if applicable to what's in the mailboxes:
  attorney-client privilege, health-adjacent data, financial-services record
  retention requirements, and similar. If any of these might apply, get
  advice specific to that sector — this page won't cover them.

## Security of the exported data

Once this tool runs, the CSV/Parquet metadata and the raw email
bodies/attachments on disk are an **unencrypted, unmanaged copy** of
potentially sensitive data, sitting outside Gmail's own access controls,
audit logging, and retention policies. Practical steps worth taking:

- Restrict filesystem permissions on `output/` to the people who actually
  need it, or encrypt that directory at rest.
- Treat the exported data with at least the same care as the original
  mailbox — arguably more, since it no longer benefits from Gmail's access
  logging.
- `client_secret.json` itself is equally sensitive: it's a standing key that
  can impersonate any mailbox in the domain. See
  [AUTH.md § Security](AUTH.md#security).

## Retention and deletion

Decide **before** you run a large export how long the output should live, and
delete it when that period is up. "We'll figure out retention later" tends to
mean "never," which is itself often the wrong answer once you've exported
personal or third-party data.

## What this tool does NOT do

- It does not anonymize or redact anything.
- It does not manage or record consent.
- It does not encrypt its output by default.
- It has no built-in retention or auto-deletion feature.
- It does not decide, on your behalf, whether a given export is lawful or
  appropriate.

All of the above are decisions and safeguards you need to put in place
yourself, outside this tool.

## Before you run this against real mailboxes

A short checklist, not a compliance program:

- [ ] Do you have sign-off from whoever is actually legally responsible —
      you, your employer, or your client?
- [ ] Have you notified affected employees or customers, if your
      jurisdiction requires it?
- [ ] Do you have a concrete plan for where the export lives and for how
      long, before you start?
- [ ] Is access to `output/` and `client_secret.json` restricted to the
      people who genuinely need it?

## One more time

This page is a prompt for the right conversations, not a substitute for
them. **Talk to a lawyer** before running this against mail that isn't only
your own.
