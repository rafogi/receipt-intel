# ADR-0002: Terraform state and CI/CD authentication

## Status
Accepted

## Context
All application infrastructure is managed by Terraform and deployed from
GitHub Actions. We need somewhere to keep state safely, a way to stop two
runs from clobbering each other, and a way for GitHub to reach AWS without
storing long-lived credentials.

## Decision
- **State:** one S3 bucket, versioned, encrypted, public access blocked,
  TLS-only, with S3-native locking (`use_lockfile`, Terraform 1.10+).
  No DynamoDB lock table.
- **Bootstrap:** the state bucket and CI roles live in a separate
  `bootstrap/` stack, applied once by an administrator from a laptop.
  Its own state is migrated into the bucket after the first apply.
- **CI authentication:** GitHub Actions OIDC with two IAM roles:
  - *Plan role:* `ReadOnlyAccess` plus state read and lock-file write.
    Trusted only for `pull_request` events in this repository.
  - *Apply role:* `AdministratorAccess`. Trusted only for jobs running in
    the `dev` GitHub environment, which is restricted to the `main` branch.

- **Trust is pinned to immutable IDs.** GitHub's OIDC subject claim for this
  repo has the form `repo:<owner>@<owner-id>/<repo>@<repo-id>:<context>`. Both
  trust policies match that exact form, so a renamed, deleted, or re-created
  repository with the same name cannot assume either role. (Found during the
  first deploy: a name-only trust policy was rejected; CloudTrail's
  `AssumeRoleWithWebIdentity` event showed the actual subject.)

## Alternatives considered
- **IAM user access keys in GitHub secrets:** rejected; long-lived keys
  can leak and must be rotated by hand.
- **A single role for plan and apply:** rejected; any pull request could
  then change infrastructure.
- **DynamoDB state locking:** no longer needed, and adds a resource.

## Consequences
- No AWS credentials are stored anywhere in GitHub.
- Code changes are reviewed as a plan before they can deploy.
- The apply role is broader than it needs to be. It is contained by the
  trust policy (environment + branch), and will be narrowed to a scoped
  policy or a permissions boundary once the full resource set is known
  (tracked for Phase 5).
- Changing the bootstrap stack requires an administrator to run it locally.
