# ManiLens — fælles regler for alle repos

## Filer der ikke reviewes
`dist/**`, `.next/**`, `out/**`, `build/**`, `*.min.*`, `*.map`, `*.lock`,
`package-lock.json`, `pnpm-lock.yaml`, billeder/video/skrifter
(`*.png *.jpg *.webp *.gif *.avif *.svg *.mp4 *.mov *.woff2`), snapshots.
Slettede filer nævnes kun ved navn.

## Regler
- Ingen hemmeligheder i kode, dokumentation, eksempelfiler eller logs. Rigtige
  nøgler hører kun hjemme i gitignored `*-secrets.php`, `.env.local` eller
  Supabase/Vercel-secrets. `*.example.*` må ikke indeholde rigtige nøgler.
- `CHANGELOG.md` opdateres i samme PR med dato og afsnittene Tilføjet / Ændret /
  Fjernet, og beskriver problemet — ikke kun ændringen.
- Links til andre domæner på vores sites skal have `target="_blank" rel="noopener"`.
  Interne links må ikke have det.
- Opdigt aldrig tal, priser, kunder eller påstande om personer eller firmaer i
  synlig tekst. Ingen store titler som "serieiværksætter" eller "ekspert".
- Next.js 16: `middleware.ts` hedder nu `proxy.ts`. AGENTS.md-blokken fra
  Next-skabelonen må ikke fjernes.
- Tekst til kunder skal være korrekt dansk/tysk; juridiske udsagn (fx UWG §7,
  GDPR) skal være præcise.

## Pre-merge checks
- CHANGELOG opdateret — error
- Udgående links i ny fane — warning
- Titelformat `type: kort beskrivelse` — warning
