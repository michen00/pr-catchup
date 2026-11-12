# Releasing

A release is a `vX.Y.Z` tag and a GitHub release for it, both made by hand. Workflows that use the action should pin the release's commit SHA, and a tag must not move once its release is published.

## Choose the version

Read the commits since the last release from a caller's side, following [Semantic Versioning](https://semver.org/):

- Major: a breaking change (`!` or `BREAKING CHANGE:` in a commit), such as a removed or renamed input or output, a changed default, or a changed meaning for an existing input.
- Minor: a new input, output, or behavior that leaves an existing caller's results unchanged (`feat`).
- Patch: a fix that keeps the interface (`fix`).

```sh
git log --oneline "$(git describe --tags --abbrev=0)..origin/main"
```

Before the first release there is no tag to describe; read the whole log instead.

## Cut the release

1. Start from an up-to-date `main` whose last CI run passed:

   ```sh
   git switch main
   git pull --ff-only origin main
   gh run list --branch main --workflow ci.yml --limit 1
   ```

2. Create a signed, annotated tag and push only that tag:

   ```sh
   git tag -s vX.Y.Z -m vX.Y.Z
   git push origin refs/tags/vX.Y.Z
   ```

   Naming the single ref keeps any other local tag off the remote, which `git push --tags` or `--follow-tags` would push.

3. Create the GitHub release from the tag:

   ```sh
   gh release create vX.Y.Z --verify-tag --generate-notes --title vX.Y.Z
   ```

   `gh` has no option for a GitHub Marketplace listing. To list the release there, draft it in the web form instead (**Releases**, then **Draft a new release**, then the existing tag) and select **Publish this Action to the GitHub Marketplace**.

4. Print the commit SHA that callers pin, and put it in the release notes:

   ```sh
   git rev-parse 'vX.Y.Z^{commit}'
   ```

## How callers pin a release

```yaml
- uses: michen00/pr-catchup@<40-character commit SHA> # vX.Y.Z
```

In a repository where Dependabot's `github-actions` ecosystem is enabled, Dependabot updates the SHA and the version comment together, so the move to a new release arrives as a pull request there.

## No floating major tag

There is no `v1` tag that moves to each new release. The action runs with a token that can push merge commits to pull request branches and approve held workflow runs. A moved tag would run new code with those permissions in every repository that uses it, before anyone there reviewed the change. A SHA pin changes only when its owner merges the change.

## A published tag must not move

If a release is wrong, publish the next patch release. Re-pointing or deleting a published tag would make the version comment in callers' pins name a commit other than the one they run. With immutable releases turned on in the repository settings, GitHub refuses to move or delete the tag of a published release.
