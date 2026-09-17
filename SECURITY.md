# Sikkerhed

Har du fundet en sårbarhed i ManiLens-workflows, motoren eller manilens.mikkelmanniche.dk, så
opret **ikke** en offentlig issue. Brug i stedet GitHubs private indberetning:
**Security → Report a vulnerability** i dette repo.

Beskriv gerne, hvad du fandt, hvordan det kan genskabes, og hvilken commit-SHA du brugte.
Du får svar hurtigst muligt.

## Grundprincipper

- Jobs med `id-token: write` kører aldrig kode fra pull requesten og henter aldrig motoren fra
  artefakter.
- Jobs med Claude-tokenet har hverken `id-token` eller skriveadgang.
- Workflows er låst til fulde commit-SHA'er, og brokeren accepterer kun godkendte SHA'er.
- manilens.mikkelmanniche.dk ser aldrig Claude-legitimationer.
