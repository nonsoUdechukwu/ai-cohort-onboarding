# Licensed lab: Sonatype Lifecycle and Repository Firewall

The [local lab](README.md) works without either product. This extension requires an organization-provided or otherwise properly entitled **Lifecycle** environment and a **Repository Firewall** entitlement for the proxy exercise. An IQ CLI download or container image does not grant either entitlement. For the Firewall steps below, use a supported IQ-connected Nexus Repository deployment provisioned by your administrator; the local Community Edition container is not assumed to be eligible. If your organization uses a different Firewall deployment model, use its corresponding vendor setup guide.

Work in a disposable training organization and repository with approval. You'll need permission to evaluate an application, and help from a policy/repository administrator to configure enforcement. Send only the sample lab dependencies and never a real application or credential to an unapproved server.

## 1. Lifecycle: evaluate the runtime dependency inventory

1. In IQ Server, create an application under the training organization with public ID `lab-greeter` (or ask your administrator to create it). Get the approved HTTPS IQ URL and an evaluator user token scoped to the application. Install an approved, compatible [IQ CLI and Java runtime](https://help.sonatype.com/en/sonatype-iq-cli.html) on the host. A server-based evaluation is not the same as a local Maven rule.
2. Before changing the dependency in the local lab, ask the policy administrator to create an **application-scoped** training policy, `Lab Commons Lang Version`. Give it one Maven **Coordinates** constraint matching group `org.apache.commons`, artifact `commons-lang3`, and version `3.19.0`, and set the **Build** action to **Fail**. Follow the [coordinate pattern syntax](https://help.sonatype.com/en/policy-constraints.html) for the installed IQ version. Don't change a shared or inherited policy. No actual vulnerability is needed for this exercise.
3. Generate an explicit scan target of the resolved production dependency JARs. Run from `labs/sonatype-devsecops/`, with `lab_mvn` defined as in the main lab. Set `IQ_CLI_JAR` to the absolute path of the approved CLI JAR and `IQ_URL` to the approved HTTPS server. This target intentionally leaves out test dependencies and the base image; it is not a full container inventory.

```bash
lab_mvn clean org.apache.maven.plugins:maven-dependency-plugin:3.9.0:copy-dependencies \
  -DincludeScope=runtime -DoutputDirectory=target/iq-deps
ls app/target/iq-deps
```

Run the evaluation below as a subshell. It keeps the token out of command arguments and removes its temporary file on exit. Use `baseline` for the first run, `remediated` for the second; the report JSON stays outside the Maven `target/` directory, which `clean` removes.

```bash
(
  set -eu
  umask 077
  label=baseline
  report="$(mktemp "${TMPDIR:-/tmp}/iq-$label.XXXXXX")"
  params="$(mktemp "${TMPDIR:-/tmp}/iq-auth.XXXXXX")"
  trap 'rm -f "$params"' EXIT
  printf 'IQ user token (username:token): '
  IFS= read -rs iq_auth
  printf '\n'
  printf '%s\n' '-a' "$iq_auth" > "$params"
  unset iq_auth
  rc=0
  java -jar "$IQ_CLI_JAR" "@$params" -i lab-greeter -s "$IQ_URL" \
    -t build -r "$report" app/target/iq-deps || rc=$?
  printf 'IQ exit %s; result file: %s\n' "$rc" "$report"
  exit "$rc"
)
```

**Checkpoint:** The baseline lists `commons-lang3-3.19.0.jar`, and the IQ report identifies that component and the Build-stage violation. A correctly matched Fail policy produces CLI exit `1`; authentication, network, or scanning errors are not gate failures. Use Maven's dependency tree to see how the component entered the build; the directory scan alone doesn't supply the dependency graph.

**Exercise:** Update the POM to `3.20.0` as in the local lab, rerun `copy-dependencies` with `clean`, and evaluate with `label=remediated`. The specific training violation should disappear. Other inherited policies may still produce violations; review those in the report rather than treating an upgrade as a blanket pass. Compare both reports, including any component security and license data shown by your entitlement. In a release pipeline, require the intended policy result before publishing the verified JAR.

## 2. Repository Firewall: audit and quarantine a new proxy request

1. Have an admin configure the [Nexus Repository to IQ connection](https://help.sonatype.com/en/firewall-audit-and-quarantine-capability.html), test it, and create a fresh Maven Central training proxy named `lab-iq-firewall`. Only use an approved lab instance where policy changes cannot affect shared proxies. Do not assign the path-routing rule from the local exercise to this proxy; the decision must come from Firewall. Keep the proxy out of shared groups.
2. In the approved Firewall policy scope, ask the administrator to create a disposable **Coordinates** policy for the same benign Maven version, `3.19.0`. Set the **Proxy** action to **Fail**. In the repository's **Firewall Audit and Quarantine** capability, enable audit and quarantine or use the matching vendor workflow for the entitled deployment. Don't request the training artifact before enforcement is active: already cached components may be audited without being quarantined.
3. In the shell, set `FIREWALL_PROXY_URL` to the admin-provided HTTPS URL ending in `/repository/lab-iq-firewall` (no trailing slash). With the approved read authentication method for that repository, request the JARs from the proxy, not the public upstream. This example assumes the training proxy permits anonymous read:

```bash
for v in 3.19.0 3.20.0; do
  curl -sS -o /dev/null -w "$v HTTP %{http_code}\n" \
    "$FIREWALL_PROXY_URL/org/apache/commons/commons-lang3/$v/commons-lang3-$v.jar"
done
```

**Checkpoint:** `3.19.0` is denied, and the proxy's Firewall report shows a quarantine entry with the training policy as the reason. `3.20.0` is delivered if no other policy blocks it. If both fail, inspect upstream connectivity, authentication, and Firewall evaluation status. A denial by itself is not evidence that Firewall quarantined a component. See [Firewall Quarantine](https://help.sonatype.com/en/firewall-quarantine.html) for cache behavior and enforcement details.

**Exercise:** Locate the violation and record its component, repository, policy, and recommended action. Compare the download denial (admission at one proxy) with the Lifecycle report (dependencies of an application at one stage). Explain how Lifecycle can still flag a component that a team resolved before the proxy started enforcing the policy.

## Cleanup

Use your administrator's process to remove the disposable proxy, training policies, and IQ application, and revoke disposable evaluator credentials. Removing the isolated proxy avoids releasing quarantined artifacts through a shared endpoint. Remove the temporary JSON reports when finished; they can contain inventory and server URLs. Return to the [local lab](README.md#5-publish-the-exact-checked-artifact-and-run-it) to publish and run the checked artifact.
