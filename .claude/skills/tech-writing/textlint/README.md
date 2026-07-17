# textlint config for `tech-writing`

This directory holds the [textlint](https://github.com/textlint/textlint) config and runtime that
the `tech-writing`, `english-tech-writing`, and `japanese-tech-writing` skills run after drafting. One
config here covers both English and Japanese prose. It is written in English, like the rest of the
repo's tooling, so a session drafting English-only prose can read it without Japanese fluency.

## Files

- `.textlintrc.json` — the rules applied. **Edit this to change what's enforced.**
- `package.json` — pins the exact versions of textlint itself and every rule (no version ranges).
- `package-lock.json` — pins every package, transitive deps included, by version and integrity hash.

## Install

```bash
npm --prefix .claude/skills/tech-writing/textlint ci --ignore-scripts
```

## Supply-chain defenses

Install with `npm ci`, not `npm install`. `npm ci` honors `package-lock.json` exactly rather than
rewriting it, and verifies each package tarball against the integrity hash (Subresource Integrity,
sha512) recorded in the lockfile — so a package whose contents are swapped under the same version
number fails on a hash mismatch. `--ignore-scripts` blocks install-time lifecycle scripts, the main
path by which an npm supply-chain attack executes.

Pinning dependencies to a git commit hash in `package.json` is deliberately not done. textlint is a
monorepo whose public npm packages are published from part of it, so pointing at a repo commit does
not resolve to the published CLI package. More fundamentally, a commit hash pins only the one
top-level package and leaves the real attack surface — the many transitive dependencies —
unprotected; the lockfile's integrity hashes pin all of them by content, a broader and stronger
guarantee. To bump a version, edit `package.json`, run `npm install` once, and always commit the
updated `package-lock.json`.

## The rules enabled today

The enabled rules fall into three groups by target language. `.textlintrc.json`'s `rules` are
ordered the same way: shared → Japanese → English.

### How rules relate to language

textlint applies every enabled rule to every file it is given; there is no per-file, by-language
routing. Most Japanese rules simply find nothing to flag in English-only text (half-width katakana
and kanji checks, for instance, have nothing to match in English), while English rules can fire on
the English words, technical terms, and code embedded in a Japanese document. If one language's
noise ever becomes a problem, splitting the config per language is an option; for now a single
config covers both.

### Shared (language-agnostic)

No language-agnostic textlint rule is enabled today. The Japanese technical-writing preset below,
`textlint-rule-preset-ja-technical-writing`, is Japanese-only as its name says. A rule that applies
equally to both languages — a future check of Markdown structure or dead links, say — would go in
this group.

### Japanese

On top of `textlint-rule-preset-ja-technical-writing` (the standard Japanese technical-writing
preset), the following are enabled:

- `spellcheck-tech-word` — orthographic variants in technical terms (e.g. インターフェース → インタフェース)
- `ja-hiragana-keishikimeishi` / `ja-hiragana-fukushi` / `ja-hiragana-hojodoushi` — formal nouns,
  adverbs, and auxiliary verbs that read better in hiragana
- `ja-hiraku` — its `keishikimeishi` / `fukushi` / `hojodoushi` checks are turned off to avoid
  double-reporting against the three `ja-hiragana-*` rules above; the remaining parts of speech
  (pronouns, adverbial particles, auxiliary adjectives, adnominals, conjunctions) stay on
- `general-novel-style-ja` — punctuation and symbol usage (only some options are on by default; see
  `.textlintrc.json`)
- `prefer-tari-tari` — the parallel "〜たり〜たりする" construction
- `@textlint-ja/textlint-rule-no-insert-dropping-sa` — misuse of inserted / dropped さ
- `no-mixed-zenkaku-and-hankaku-alphabet` — mixing full-width and half-width alphabet

All four kanji-range rules are pinned in `package.json` but left disabled (`false`) in
`.textlintrc.json`:

- `ja-joyo-or-jinmeiyo-kanji` (kanji outside 常用漢字 + 人名用漢字) and `joyo-kanji` (kanji outside
  常用漢字 alone) are disabled because they flag ordinary technical-writing words whose kanji fall
  outside those sets — 推敲, 繋がる, 敷衍, 罫線, 腑分け, and the like. The `japanese-tech-writing`
  policy is to clear a finding by revising the prose, not by loosening the config; but these words
  cannot be reasonably rewritten, so the rule would either stall drafting or force a growing
  allow-list. Leaving them off keeps the skill's own prose (and the existing Japanese docs) passing
  textlint, which the policy requires.
- `ja-kyoiku-kanji` (educational kanji) and `ja-allowed-kanji` (an explicit allow-list) restrict
  kanji to an even narrower set than 常用漢字, stricter still. Turn one on individually, with an
  allow-list, only if a specific document needs a bounded kanji set.

### English

- `write-good` — English style issues such as passive voice and wordiness
- `stop-words` — filler words, buzzwords, and clichés
- `unexpanded-acronym` — whether an acronym is expanded in the document
- `abbr-within-parentheses` — abbreviation-in-parentheses form
- `alex` — gender-, race-, or religion-insensitive phrasing
- `ukraine` — Ukrainian place and person names spelled the Russian way

`textlint-rule-ginger` (English grammar/spell-check via the Ginger API) is intentionally left out:
its `gingerbread` → `request` → `form-data`/`qs`/`tough-cookie`/`uuid` chain reports two critical
and three moderate `npm audit` findings with no fix available, and `gingerbread` itself is marked
unsupported on npm.

## Changing the rules

Clear a finding by revising the prose, not by loosening the config: textlint takes priority over the
prose norms, so a rule is not turned off or a threshold raised merely to dodge a finding on text you
have written (this is the `japanese-tech-writing` policy, and it holds for English prose too). Change
the config only for structural reasons — adopting or retiring a rule, or stopping a rule from
double-reporting what another already covers — as with the kanji-range rules above.

Edit `rules` in `.textlintrc.json`. To turn off one rule from the preset, override just that rule by
name rather than expanding the whole preset:

```json
{
  "rules": {
    "preset-ja-technical-writing": {
      "sentence-length": false
    }
  }
}
```

To add another preset or rule, add it to `devDependencies` in `package.json`, then register it under
`rules` in `.textlintrc.json`. Always pin the version, so every environment reports the same
findings. Place the entry in its target-language group (shared, Japanese, or English) and add a line
to "The rules enabled today" above, so the config and its description stay in step.
