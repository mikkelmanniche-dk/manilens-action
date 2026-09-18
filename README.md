# ManiLens action

> **Copyright © 2026 Mikkel Manniche. Alle rettigheder forbeholdt.** Ikke open source. Repoet er kun
> offentligt, så GitHub Actions kan hente motoren. Kopiering, ændring, videregivelse og anden brug end
> via ManiLens for godkendte brugere er ikke tilladt. Se [LICENSE](LICENSE).

Genbrugelige GitHub Actions-workflows, der giver pull requests et AI-review fra `manilens[bot]`.
Reviewet kører i **dit eget repo** med **dit eget Claude-abonnement**.

## Hvem kan bruge det

ManiLens er et privat projekt for en lille kreds. Du skal have adgang på
[manilens.mikkelmanniche.dk](https://manilens.mikkelmanniche.dk), og ManiLens-appen skal være
installeret på repoet.

- Kun **private, ikke-kommercielle** projekter. Aldrig arbejdskode eller kunders kode.
- Du bruger **dit eget** Claude-abonnement. Tokenet ligger som en secret i dit repo og forlader
  aldrig GitHub Actions. manilens.mikkelmanniche.dk modtager, gemmer og videresender aldrig
  Claude-legitimationer.

## Sådan virker det

1. `forbered` spørger manilens.mikkelmanniche.dk med et GitHub OIDC-token, om repoet må reviewes,
   og får den motor-SHA, der er godkendt.
2. `tjek` kører faste scannere på PR'ens kode uden hemmeligheder.
3. `review` læser koden med Claude (ingen shell, ingen skriveadgang).
4. `post` får et kortlivet token til netop dit repo og poster reviewet.

Motoren hentes altid fra dette repo på den godkendte, fulde commit-SHA.

## Opsætning

Opsætningen vises på din konto på manilens.mikkelmanniche.dk, med den SHA du skal låse til.
Workflow-filen i dit repo skal pege på en **fuld commit-SHA**, aldrig på en branch:

```yaml
uses: mikkelmanniche-dk/manilens-action/.github/workflows/review.yml@<40-tegns-sha>
```

## Sikkerhed

Se [SECURITY.md](SECURITY.md).

## Licens

Proprietær, alle rettigheder forbeholdt — se [LICENSE](LICENSE). Adgang på manilens.mikkelmanniche.dk giver
kun ret til at kalde workflowene fra egne private, ikke-kommercielle repos, så længe godkendelsen gælder.

## Tredjepartsværktøjer

Licensen ovenfor gælder ikke de værktøjer og regler, `tjek` henter under kørslen. De ligger ikke i dette repo og
hentes fra deres egne udgivelser på faste versioner (se `action/install_scanners.sh`), bl.a. opengrep (LGPL-2.1),
ruff, zizmor, oxlint, golangci-lint, trivy, osv-scanner, actionlint og gitleaks under deres egne licenser.

Opengreps regler hentes fra [opengrep/opengrep-rules](https://github.com/opengrep/opengrep-rules) på commit
`f1d2b562b414783763fd02a6ed2736eaed622efa` og er omfattet af denne licensbetingelse:

> "Commons Clause" License Condition v1.0
>
> The Software is provided to you by the Licensor under the License, as defined below, subject to the following
> condition.
>
> Without limiting other conditions in the License, the grant of rights under the License will not include, and the
> License does not grant to you, the right to Sell the Software.
>
> For purposes of the foregoing, "Sell" means practicing any or all of the rights granted to you under the License to
> provide to third parties, for a fee or other consideration (including without limitation fees for hosting or
> consulting/ support services related to the Software), a product or service whose value derives, entirely or
> substantially, from the functionality of the Software. Any license notice or attribution required by the License
> must also include this Commons Clause License Condition notice.
>
> Software: semgrep-rules (https://github.com/semgrep/semgrep-rules)
> License: LGPL 2.1 (GNU Lesser General Public License, Version 2.1)
> Licensor: Semgrep, Inc. (https://semgrep.dev)

ManiLens tager ingen betaling og leverer ingen betalt ydelse.
