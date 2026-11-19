# DevSecOps lab: from source to a released Docker image

**Time:** 90–120 minutes for the local track. Allow another 45–60 minutes for the [licensed track](LICENSED-TRACK.md).

You'll resolve a Java dependency through Nexus, make two different build gates fail and then pass, publish the checked JAR, and build a Docker image from the artifact you downloaded back. No portal, Azure account, or Sonatype paid entitlement is needed for the local track.

| Tool | What it decides here |
|---|---|
| Docker and Compose | What runs, how containers find each other, and which state survives restarts |
| SonarQube Community Build (free, open source) | Whether first-party code meets a quality gate based on test coverage |
| Nexus Repository Community Edition (free, self-hosted, with [usage limits](https://help.sonatype.com/en/ce-onboarding.html)) | Where dependencies are fetched and cached, and where built releases are stored |
| Sonatype Lifecycle (licensed, evaluated by IQ Server) | Whether an application's third-party components comply with policy at a given stage |
| Sonatype Repository Firewall (licensed separately or through an entitled bundle) | Whether a component can enter through a protected proxy |

Without a license, Maven Enforcer and a Nexus path-routing rule let you practice the decisions in the last two rows. Neither exercise queries Sonatype intelligence, detects vulnerable packages, or implements Firewall quarantine.

```text
Maven builder ──resolve──> Nexus group ──> Maven Central proxy ──> Central
      │
      ├──source + JaCoCo coverage──> SonarQube ──> PostgreSQL
      ├──dependency policy──> local Enforcer or licensed IQ evaluation
      └──publish checked JAR──> Nexus hosted release ──download──> Docker build
```

## 1. Start the stack and inspect it

Use Docker Desktop or Docker Engine with Compose v2 and Linux containers, a browser, `curl`, and a Bash or Zsh terminal (WSL2 is also suitable). Allocate about **10 GB RAM and 15 GB free disk** for Docker. Allow outbound HTTPS to Docker Hub and Maven Central. Java and Maven run in containers, so neither needs to be installed on the host. Ports 9000 and 8081 must be free.

```bash
cd labs/sonatype-devsecops
docker version                       # both Client and Server should be shown
docker compose config -q
docker compose up -d
docker compose ps
docker compose logs --tail=40 sonarqube nexus
```

Allow several minutes on first boot. Repeat these probes until both are ready:

```bash
curl -fsS http://127.0.0.1:9000/api/system/status
curl -fsS -o /dev/null -w 'Nexus HTTP %{http_code}\n' \
  http://127.0.0.1:8081/service/rest/v1/status
```

**Checkpoint:** SonarQube reports `UP`; Nexus returns `200`. The browser uses host loopback addresses. Containers use Compose service names such as `nexus:8081`; `localhost` inside the builder would point back to the builder itself. `sonar-db` has no host port. The `./app` bind mount shares source and build output with your editor; named volumes keep databases, repositories, and Maven's cache across container restarts. The `builder` is a one-off job, not a long-running service.

These are disposable local services, not production settings. Do not expose them on a public interface, reuse the lab database password, or put real credentials in the lab files.

## 2. Configure Nexus and resolve dependencies

1. Open [Nexus](http://127.0.0.1:8081) and sign in as `admin`. Retrieve the generated password with `docker compose exec -T nexus cat /nexus-data/admin.password`. Set a new password when prompted and enable anonymous reads for this loopback-only instance.
2. Under **Settings → Repository → Repositories**, find `maven-central` (`proxy`) and `maven-public` (`group`). Set the proxy's remote storage URL to `https://repo.maven.apache.org/maven2/` and verify the group includes the proxy. The proxy fetches and caches upstream files; the group gives developers one URL for multiple members.
3. Create `lab-releases` as **maven2 (hosted)**, with version policy **Release**, default blob store, and deployment policy **Disable redeploy**. Keep it separate from the group for now. Hosted repositories store your uploads; they do not fetch dependencies from Central.
4. Under **Security → Roles**, create a Nexus role named `lab-publisher`. Assign the `nx-repository-view-maven2-lab-releases-` privileges ending in `browse`, `read`, `add`, and `edit`. Under **Security → Users**, create an active local user `lab-publisher` with that role and a unique lab password. Use that identity for publication, not `admin`.

Run this in your lab terminal. The `lab_mvn` function is only a shorthand; redefine it if you open another shell.

```bash
lab_mvn() { docker compose run --rm -T builder mvn -B -s settings.xml "$@"; }
lab_mvn clean verify
lab_mvn org.apache.maven.plugins:maven-dependency-plugin:3.9.0:tree -Dscope=runtime
```

**Checkpoint:** One test passes; download lines point at `http://nexus:8081/repository/maven-public/`; the runtime tree includes `org.apache.commons:commons-lang3:jar:3.19.0`. In Nexus **Browse → maven-central**, find `org/apache/commons/commons-lang3`. The downloaded copies are in the proxy, not the group.

**Exercise:** Run `lab_mvn -o verify`. It uses Maven's local cache and does not prove Nexus served anything. Now run the following with a separate Maven local repository and read the download log. Nexus should already have most of the files from the first build.

```bash
lab_mvn -Dmaven.repo.local=/tmp/lab-fresh-m2 verify
```

## 3. Make the SonarQube gate fail, then fix it

1. Open [SonarQube](http://127.0.0.1:9000), sign in with `admin` / `admin`, and change the password.
2. Create a local project named **Lab Greeter** with key `lab-greeter`. Under **Quality Gates**, create `Lab Coverage`; remove the copied conditions so the exercise measures one thing. Add **Overall Code → Coverage → is less than → 95%**. Assign that gate to `Lab Greeter`, using the gate's project selector or the project's quality gate settings. Choosing overall code avoids the minimum new-lines threshold on this small sample.
3. In **My Account → Security**, generate a **Project Analysis Token** for `Lab Greeter`. Paste it into the hidden prompt below; don't save it in source or put it directly in a shell command.

```bash
printf 'SonarQube token: '
IFS= read -rs SONAR_TOKEN
printf '\n'
export SONAR_TOKEN

lab_analyze() {
  docker compose run --rm -T -e SONAR_TOKEN builder mvn -B -s settings.xml "$@" \
    org.sonarsource.scanner.maven:sonar-maven-plugin:sonar \
    -Dsonar.projectKey=lab-greeter -Dsonar.qualitygate.wait=true
}
lab_analyze clean verify
```

**Checkpoint:** Maven's tests pass, but the scanner exits nonzero after reporting a failed quality gate. SonarQube imports JaCoCo XML from `app/target/site/jacoco/jacoco.xml`. Open **Measures → Coverage**, find `Greeter.java`, and identify the untested branch.

**Exercise:** Add a test inside `GreeterTest` for the formal greeting. For example:

```java
@Test
void greetsFormally() {
    assertEquals("Good day, Ada.", Greeter.message(" Ada ", true));
}
```

Rerun `lab_analyze clean verify`. Both tests and the quality gate should pass. The scanner waits for server-side processing so a failed gate can stop publication in a pipeline. The exercise disables SCM analysis because the builder only mounts `app/`, not the parent Git worktree.

## 4. Practice dependency policy and proxy controls

If you have Sonatype entitlements, the [licensed track](LICENSED-TRACK.md) includes actual Lifecycle and Firewall exercises. Use approved lab instances only. If you also want a before/after IQ report, run its baseline evaluation **before** editing the dependency below.

### Application policy: local stand-in for Lifecycle

```bash
lab_mvn -Ptraining-policy verify
```

**Checkpoint:** The build fails with `training-version-policy` for `commons-lang3:3.19.0`, even though the SonarQube gate passed. This is a deliberately hand-written rule for the exercise, not a claim about a CVE or a license violation. Inspect the `training-policy` profile in [the POM](app/pom.xml) to see the banned version.

**Exercise:** In `app/pom.xml`, change `commons-lang3.version` to `3.20.0`. Run the runtime dependency tree again, then `lab_analyze -Ptraining-policy clean verify`. Expect the resolved tree to show `3.20.0` and both gates to pass. In actual Lifecycle, inspect the component, dependency path, violation reason, and available remediation options before selecting a replacement. A passing code quality gate does not clear a dependency policy violation.

### Proxy admission: free path rule, not Repository Firewall

1. In Nexus **Settings → Repository → Routing Rules**, create `lab-block-version`, mode **BLOCK**, with matcher `^/org/apache/commons/commons-lang3/3\.19\.0/.*$`. In the rule tester, compare `/org/apache/commons/commons-lang3/3.19.0/commons-lang3-3.19.0.pom` with the equivalent path for `3.20.0`.
2. Create a new **maven2 (proxy)** named `lab-firewall-proxy` with version policy **Release**, remote storage `https://repo.maven.apache.org/maven2/`, and the new routing rule. Leave this proxy **out of** `maven-public` so it won't affect the rest of the lab.
3. Request the two harmless POMs from that proxy directly:

```bash
proxy=http://127.0.0.1:8081/repository/lab-firewall-proxy
for v in 3.19.0 3.20.0; do
  curl -sS -o /dev/null -w "$v HTTP %{http_code}\n" \
    "$proxy/org/apache/commons/commons-lang3/$v/commons-lang3-$v.pom"
done
```

**Checkpoint:** `3.19.0` receives a non-2xx response; `3.20.0` receives `200`. If both fail, check the rule preview, anonymous access, and upstream connectivity before attributing either result to the rule. A path rule acts on a request to one proxy. It cannot inspect component intelligence, protect downloads already in a developer's local cache, or produce quarantine records or waivers.

## 5. Publish the exact checked artifact and run it

After both gates pass, do not edit the source or rebuild the JAR before publishing. If you ran the licensed dependency exercise after your last build, rerun `lab_analyze -Ptraining-policy clean verify` to regenerate the checked JAR. The deploy plugin uploads the existing shaded JAR (it contains its runtime dependency) and POM. The settings file's server ID matches the `-DrepositoryId` below, so Maven can pick the right credentials.

```bash
export LAB_NEXUS_USER=lab-publisher
printf 'Nexus publisher password: '
IFS= read -rs LAB_NEXUS_PASSWORD
printf '\n'
export LAB_NEXUS_PASSWORD

docker compose run --rm -T -e LAB_NEXUS_USER -e LAB_NEXUS_PASSWORD builder \
  mvn -B -s settings.xml org.apache.maven.plugins:maven-deploy-plugin:3.1.4:deploy-file \
  -DrepositoryId=lab-releases -Durl=http://nexus:8081/repository/lab-releases/ \
  -Dfile=target/lab-greeter-1.0.0.jar -DpomFile=pom.xml
unset LAB_NEXUS_PASSWORD
```

**Checkpoint:** In **Browse → lab-releases**, locate version `1.0.0`. Trying to publish that same version again should be rejected because redeployment is disabled. A new release should get a new version, not overwrite the old one.

Use this single chained command so a failed download or byte comparison cannot silently build an image:

```bash
mkdir -p app/target/released &&
curl -fsS -o app/target/released/app.jar \
  http://127.0.0.1:8081/repository/lab-releases/org/example/cohort/lab-greeter/1.0.0/lab-greeter-1.0.0.jar &&
cmp app/target/lab-greeter-1.0.0.jar app/target/released/app.jar &&
docker build -t cohort-lab-greeter:1.0.0 app &&
docker run --rm --network none cohort-lab-greeter:1.0.0
```

**Checkpoint:** `cmp` is silent and the container prints `Hello, cohort!`. The image is the packaged runtime; `docker run` creates a container from it. The Dockerfile runs Java as a non-root user and copies only the released JAR. `--network none` shows that this app does not fetch dependencies at runtime.

**Exercise:** Run `docker run --rm --network none -e JAVA_TOOL_OPTIONS='-Dlab.name=Ada -Dlab.formal=true' cohort-lab-greeter:1.0.0`. Expect `Good day, Ada.` after Java's environment notice. Stop and recreate the Compose stack with `docker compose down` then `docker compose up -d`: which state persists, and why?

## Troubleshooting

| Symptom | What to check |
|---|---|
| Docker client is present but can't reach the server | Start Docker Desktop or the Engine; rerun `docker version`. |
| Port already allocated | Stop the conflicting process, or change only the host-side port in `compose.yaml` and update host URLs. Leave container-side ports and DNS names unchanged. |
| SonarQube stops during bootstrap | Read `docker compose logs sonarqube sonar-db`. Check memory and the [SonarQube host requirements](https://docs.sonarsource.com/sonarqube-community-build/server-installation/pre-installation/linux). On native Linux, check `vm.max_map_count` in particular; use the documented settings on the Docker host. |
| Maven cannot resolve plugins or gets HTTP 401 | Check anonymous access, the group URL, and group membership. If the Nexus proxy cannot reach `https://repo.maven.apache.org/maven2/`, check outbound DNS/HTTPS from Docker. After correcting the cause, retry Maven with `-U`. |
| Scanner gets HTTP 401/403 or times out | Check token scope, project key, and `docker compose logs sonarqube`. Confirm the analysis task ran before interpreting a failed invocation as a failed gate. |
| Coverage looks unchanged | Run `clean verify` before analysis; confirm JaCoCo XML exists and the correct gate is assigned to the project. |
| Upload gets 401/403 or fails on retry | Confirm publisher credentials, scoped privileges, and matching server ID. If an artifact already exists or a prior upload was partial, inspect the hosted repository before attempting another version. |
| Linux host cannot remove `app/target` | The default builder runs as root. To remove only its build output, use `docker compose run --rm -T builder rm -rf /workspace/target`. |

## Cleanup

`docker compose down` stops and removes the lab containers and network but keeps data. To wipe the lab's Nexus repositories, SonarQube projects, PostgreSQL data, and Maven cache as well, run:

```bash
docker compose down --volumes
docker image rm cohort-lab-greeter:1.0.0
unset SONAR_TOKEN LAB_NEXUS_USER LAB_NEXUS_PASSWORD
```

This leaves `app/target/` and your exercise edits on disk. Remove the ignored build output if desired and review the test and dependency edits before discarding them. No global Docker prune is needed.
