# ManiLens action

> **Copyright © 2026 Mikkel Manniche. Alle rettigheder forbeholdt.** Ikke open source. Repoet er kun
> offentligt, så GitHub Actions kan hente motoren. Kopiering, ændring, videregivelse og anden brug end
> via ManiLens for godkendte brugere er ikke tilladt. Se [LICENSE](LICENSE).

Genbrugelige GitHub Actions-workflows med scannere og valgfrit AI-review fra `manilens[bot]`.
Scannerne kører i **dit eget repo**. AI-review bruger **dit eget Claude-abonnement**.

## Hvem kan bruge det

ManiLens er et privat projekt for en lille kreds. Du skal have adgang på
[manilens.mikkelmanniche.dk](https://manilens.mikkelmanniche.dk), og ManiLens-appen skal være
installeret på repoet.

- Kun **private, ikke-kommercielle** projekter. Aldrig arbejdskode eller kunders kode.
- Du bruger **dit eget** Claude-abonnement. Tokenet ligger som en secret i dit repo og forlader
  aldrig GitHub Actions. manilens.mikkelmanniche.dk modtager, gemmer og videresender aldrig
  Claude-legitimationer.

## Sådan virker det

1. `forbered` kontrollerer adgang og den godkendte motor-SHA. Scannerne har et selvstændigt loft på 50 kørsler pr. ejer pr. UTC-døgn.
2. `tjek` opdager delprojekter og relevante scannere, installerer kun de valgte værktøjer og kører uden hemmeligheder. Npm- og Composer-scripts samt Python-tests i undermapper indgår; manglende afhængigheder vises som fejl.
3. `configuration` afgør kun, om et Claude-token findes. `ai_admit` kontrollerer derefter AI-kvoten i et separat job uden Claude-token.
4. `review` læser koden med Claude (ingen shell eller skriveadgang), når token og kvote er tilgængelige.
5. `post` opdaterer én PR-oversigt med scannerresultater, eventuelle AI-fund og årsagen til manglende kørsel. Uden et fuldført review afgives ingen merge-godkendelse; det samlede ManiLens-tjek forbliver blokeret.

Scannere fortsætter uden Claude-token og efter AI-dagsgrænsen. Opsætningssiden viser verificeret workflow og seneste kørsel med tidspunkt. En scannerkørsel eller et tokennavn beviser ikke en Claude-forbindelse: den markeres først efter et vellykket modelkald. Statusmetadata opbevares i højst 30 dage plus næste oprydning.

Rapporten viser version, omfang, resultat, fund og varighed pr. tjek. `passed` betyder gennemført uden rapporterede fund, `findings` betyder fund, `error` betyder ufuldstændigt eller fejlet tjek, og `skipped` har en forklaring. Typecheck/testfejl og ufuldstændig scannerdækning kan ikke overtrumfes af modellen.

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
