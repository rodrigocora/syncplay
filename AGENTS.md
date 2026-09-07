# AGENTS.md

## 1. Project Conventions

### Meta

* There's always to exist a meta directory and it's file filled as it should in the root of every project.
* Do not comment inside short code blocks.

### README.md

* Always creates and fill a README.md files to all roles.

### Ansible

#### FQCN / Module Names

* Do not use FQCNs such as `ansible.builtin.command`.
* Follow the project's convention even when FQCN would otherwise be recommended. Recommended is not the same as needed.
* Do not touch vault related file.
* Use the module tmpdir when it's better to keep the idempotency or even save a few lines.
* Do not add SPDX license headers, remove them and never re-introduce them.
* Assume gather_fact: true

### Code Style

* Keep code concise, readable, and maintainable.
* Avoid unnecessary comments.
* Comments should explain **why**, not describe obvious code.
* Put substantial explanations in the README or documentation instead of inline code comments.
* Prefer existing project patterns over introducing new conventions.
* Look for a TODO.md in the root of the project for new tasks.

### Databases

* Store passwords for scripts the safest way available. Not need to change the course of the task.

#### SQLite

* All sqlite database file must .sqlite as extension.

---

## 2. Technical Context

This project is primarily concerned with infrastructure automation and systems administration.

Technologies commonly used in this environment include:

### Automation and Programming

* Ansible
* Ansible Roles
* Jinja2
* Python
* Shell / Bash
* YAML
* SQL

### Databases

* Oracle Database
* PostgreSQL
* MySQL
* MongoDB
* ClickHouse
* TimescaleDB

### Infrastructure and Platforms

* Linux, particularly Red Hat-family distributions and Fedora
* Docker
* Docker Compose
* Docker Swarm
* Kubernetes
* Oracle Cloud Infrastructure (OCI)
* Azure
* Amazon AWS
* Terraform
* Git

### Monitoring and Operations

* Zabbix
* Database performance and telemetry
* Backup and recovery
* Infrastructure configuration management

### Engineering Preferences

* Prefer open-source solutions when practical.
* Prefer automation over repetitive manual procedures.
* Prefer solutions that are easy to test, reproduce, maintain, and troubleshoot.
* Prefer established project tooling over introducing unnecessary dependencies.
* For database work, prioritize correctness, observability, recoverability, and predictable operational behavior.

These technologies provide context, not mandatory dependencies. Do not introduce a technology merely because it appears in this list.

---

## 3. Fedora Playground VM

This project defines a reproducible Fedora development/playground environment using **Vagrant**.

The VM is intended to provide an isolated environment for development, testing, and experimentation, particularly with software produced or modified by AI agents.

### Virtualization

* On Windows, use the **Hyper-V** Vagrant provider.
* On Linux, use **libvirt/KVM** when available.
* Keep the Vagrant configuration portable between hosts where reasonably possible.
* Keep host-specific virtualization details isolated from the general VM configuration.
* Do not assume that the host filesystem should be shared with the VM.

### Guest User

The Fedora guest must have a user named `dev`.

The `dev` user must:

* Be created automatically during provisioning.

* Have a normal interactive shell.

* Have passwordless `sudo`.

* Have SSH public-key authentication configured.

* Use the public SSH keys published at:

  `https://github.com/rodrigocora.keys`

* Never require a private SSH key to be stored in the repository or copied into the VM.

### Host Isolation

The playground VM must not depend on the host's personal environment.

* Do not mount the host `$HOME` automatically.
* Do not expose unrelated host filesystems to the VM.
* Do not copy host credentials into the VM.
* Do not store private credentials in the repository.
* Do not assume that production credentials are available to the VM.
* Prefer explicit, narrowly scoped access to external resources.

### External Infrastructure

Oracle Database 26ai is an existing external service.

* Do not provision or virtualize Oracle Database as part of this project.
* The VM must be able to communicate with the existing Oracle Database when required by the development workflow.
* Oracle remains external to the disposable VM.

S3 is transient storage for documentation.

* S3 is not the permanent source of truth.
* Do not design the playground around persistent S3 access unless explicitly required.
* The Oracle Database is the intended source of truth for the relevant documentation/knowledge infrastructure.

### Provisioning

Initial provisioning should install the basic packages and tools required for a useful Fedora development environment.

Provisioning must be:

* Idempotent.
* Reproducible.
* Maintainable.
* Safe to run repeatedly.
* Designed so that normal `vagrant up` does not unnecessarily reinstall packages or recreate existing state.

Do not unnecessarily install OpenCode, ACP, MCP components, or Oracle tooling unless explicitly requested or required by the repository.

The development environment will eventually be used for:

* OpenCode or another coding agent
* ACP
* MCP development
* Oracle Database 26ai integration
* General development and testing
* AI-generated code experimentation

These components should be added incrementally rather than prematurely.

### VM Persistence and Snapshots

The VM is disposable, but normal development state should not have to be rebuilt unnecessarily.

Distinguish between:

* `vagrant up`
* `vagrant halt`
* `vagrant provision`
* `vagrant destroy`
* snapshot creation
* snapshot restoration

Snapshots are an important part of the development workflow.

They should preserve, as supported by the provider:

* Installed packages
* VM configuration
* Development files
* User configuration
* Other state inside the VM

The workflow should support creating a known-good state and restoring it after experimentation.

Example workflow:

```text
vagrant up

# Configure the environment and work normally.

vagrant snapshot save clean

# Make experimental changes.

vagrant snapshot save before-experiment

# Experiment further.

vagrant snapshot restore before-experiment
```

Do not design the workflow around destroying and recreating the VM after every experiment.

`vagrant destroy` should be treated as a genuinely destructive operation.

### Makefile Interface

The repository may provide a Makefile as a convenient interface over Vagrant.

Preferred targets include:

```text
make create
make shell
make status
make provision
make snapshot
make restore
make destroy
make recreate
```

Map these to Vagrant operations where appropriate.

Do not replace or modify existing Makefile functionality blindly. Inspect and understand the existing Makefile before changing it.

### Reproducibility

The environment should eventually be reproducible from the repository with:

* Vagrant configuration
* Provisioning configuration
* Required scripts
* Documentation
* Clearly defined external dependencies

Development state that is intentionally preserved should use VM persistence or snapshots rather than undocumented manual steps.

---

## 4. Objective-Driven Behaviour

The goal is to maximize **verifiable progress**, not to explore every imaginable scenario.

The default operating loop is:

**Define → Prioritize → Act → Verify → Update → Iterate → Stop**

### Define the Objective

* Identify the concrete outcome requested.
* Determine what constitutes success.
* Identify the smallest useful verification that can prove success.
* Separate:

  * **known facts**
  * **unknowns**
  * **assumptions**
* Treat assumptions as temporary.
* Replace assumptions with evidence as soon as practical.
* Resolve only ambiguity that materially affects the next action.
* Do not expand the scope because unrelated issues are discovered unless they block the objective.

The objective is the user's requested result, not an opportunity to redesign the entire system.

### Prioritize

* Consider only the **1–3 most plausible and useful** hypotheses or approaches.
* Prioritize using:

  1. probability of success
  2. impact
  3. cost and complexity
  4. ease of verification
* Distinguish **possible** from **probable**.
* Prefer hypotheses that can be tested cheaply.
* Do not spend significant effort on low-probability edge cases without evidence that they are relevant.
* Prefer an incremental solution that can be verified over a theoretically complete solution that cannot yet be tested.

Do not attempt to eliminate every uncertainty before taking the first useful action.

### Act

* Choose the approach with the best probability-to-cost ratio and execute it.
* Prefer the **smallest action capable of distinguishing between the leading hypotheses**.
* Prefer inspection, reproduction, or a cheap test over speculative changes.
* When the cause is uncertain, **diagnose before applying broad remediation**.
* Avoid making multiple unrelated changes in the same iteration when a single change can test the hypothesis.
* Prefer reversible and low-impact actions while the cause is uncertain.
* Preserve the existing working state whenever practical.
* For infrastructure changes, avoid destructive or irreversible operations unless they are required and sufficiently justified.
* Do not perform extensive upfront analysis unless the task involves:

  * high risk
  * irreversible changes
  * security implications
  * materially ambiguous requirements

The first action should usually produce information, progress, or both.

### Verify

Never assume that an action worked.

Verify using concrete evidence such as:

* command output
* test results
* playbook execution
* `ansible-lint`
* unit or integration tests
* service status
* configuration inspection
* rendered templates
* file inspection
* database queries
* logs
* before/after comparison
* API responses

**Observed evidence is the source of truth.**

If evidence contradicts the current hypothesis:

1. trust the evidence
2. discard or revise the hypothesis
3. choose the next action from the new evidence

Never claim to have executed, tested, verified, inspected, or observed something that was not actually done.

### Update

After every meaningful action:

* Record what was confirmed.
* Record what was disproved.
* Discard hypotheses contradicted by evidence.
* Reassess only the hypotheses relevant to the new result.
* Use the result to determine the next action.
* Do not repeat an already completed analysis or verification without new evidence.
* Prefer information that materially reduces uncertainty.

The process should converge toward an answer rather than repeatedly reconsidering the same possibilities.

### Iterate

Repeat:

**Choose → Act → Verify → Update**

For each iteration:

* Reassess only the **1–3 best current hypotheses or approaches**.
* Make a meaningful change before retrying.
* Do not repeat the same approach without new evidence or a justified modification.
* If a test fails, use the failure to refine the next attempt.
* After **two failed attempts based on the same hypothesis**, switch to a structurally different approach.
* Do not restart the reasoning process from scratch after every result.

A failed attempt is useful only if the next attempt incorporates what was learned from it.

### Stop

Stop when the objective is **reached and verified**.

* Prefer a correct, tested, sufficient solution over a theoretically perfect one.
* Do not continue investigating merely because additional possibilities exist.
* Once sufficient evidence establishes the requested outcome, stop.
* Do not pursue unrelated improvements unless requested.
* Do not chase hypothetical edge cases without evidence that they affect the objective.
* Do not refactor working code solely for theoretical consistency when it is outside the requested scope.

**A verified, sufficient solution is complete.**

---

## 5. Ansible-Specific Behaviour

When working on Ansible roles, prioritize:

1. correctness
2. idempotency
3. predictability
4. readability
5. minimal scope

### Prefer

* Existing role structure and conventions
* Existing variables and defaults
* Existing handlers
* Existing templates
* Native Ansible modules
* Idempotent tasks
* Small, focused changes
* Validation through `ansible-lint` and execution/testing

### Avoid

* Shell or `command` when a suitable Ansible module exists
* Unnecessary task duplication
* Unnecessary variable indirection
* Complex Jinja expressions when simpler logic is available
* Broad refactors while fixing a specific problem
* Adding abstractions before they are justified
* Making a role less predictable in order to handle hypothetical cases

When `command` or `shell` is necessary, verify whether the operation is actually idempotent and handle that explicitly where appropriate.

---

## 6. Infrastructure and Database Behaviour

When working with infrastructure, databases, containers, or orchestration:

* Prefer observing the current state before changing it.
* Verify configuration at the layer where the problem actually occurs.
* Do not assume that an error message identifies the root cause.
* Distinguish configuration errors from runtime errors, dependency errors, permissions, networking, and application/database behavior.
* Prefer targeted diagnostics over broad system changes.
* For database changes, consider operational impact, locking, restart requirements, persistence, and rollback before execution.
* For production-impacting changes, increase the verification threshold before modifying state.
* Prefer reproducible commands and documented procedures over ad-hoc manual fixes.

For Docker, Kubernetes, Terraform, OCI, and similar infrastructure tools, verify both:

* the desired configuration
* the resulting runtime state

A successful configuration command does not by itself prove that the desired state was achieved.

---

## 7. Security and Safety

Security-sensitive operations require a higher verification threshold.

* Do not expose secrets in output, logs, templates, commits, or generated files.
* Do not print credentials merely for debugging.
* Avoid weakening authentication, authorization, TLS, filesystem permissions, or network controls as a first troubleshooting step.
* Prefer diagnosing the actual failure before relaxing security controls.
* Treat destructive operations as high-risk.
* Verify the target before performing operations that could affect data, infrastructure, or production services.

When a risky action is necessary, make the risk explicit and verify the preconditions first.

---

## 8. Change Discipline

Keep changes proportional to the objective.

### For a bug or failure

Prefer:

**Reproduce → Inspect → Hypothesize → Test → Fix → Verify**

### For a new feature

Prefer:

**Define → Implement minimally → Test → Verify**

### For refactoring

Prefer:

**Establish current behavior → Make one coherent change → Verify no regression**

Do not combine unrelated fixes, refactors, upgrades, formatting changes, or architectural changes in the same change unless the task explicitly requires them.

---

## 9. Default Decision Rule

When uncertain about what to do next, choose the action that:

* advances the objective
* produces useful evidence
* has low cost
* has low risk
* is easy to reverse
* reduces uncertainty

Do not choose an action merely because it is theoretically possible.

**Evidence beats assumptions.**
**Progress beats speculation.**
**Verification beats expectation.**
**Sufficient beats perfect.**
