#!/usr/bin/env node
/**
 * Vendor the LEX auth contract v2 client into a consumer repo, or check a vendored copy for drift.
 *
 * Canonical home: lex-app `lex/contracts/auth/v2/scripts/vendor-lex-auth-client.mjs`. Consumers keep
 * an identical copy at `scripts/vendor-lex-auth-client.mjs`. No dependencies (Node >= 18).
 *
 * Vendor (from the consumer repo root):
 *   node scripts/vendor-lex-auth-client.mjs --from ../lex-app [--ref v2.5.0] [--dest src/vendor/lex-auth-client]
 *     --from  a lex-app checkout, or the contract directory itself (lex/contracts/auth/v2)
 *     --ref   read the files from this git ref of the lex-app checkout instead of its working tree
 *
 * Check (CI):
 *   node scripts/vendor-lex-auth-client.mjs --check [--dest src/vendor/lex-auth-client] [--from … [--ref …]]
 *     Fails (exit 1) when a vendored file was edited by hand, a file is missing or extra, or — with
 *     --from — the vendored copy differs from the source.
 */
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

const CONTRACT_DIR = path.join('lex', 'contracts', 'auth', 'v2')
const DEFAULT_DEST = path.join('src', 'vendor', 'lex-auth-client')
// Source path (relative to the contract dir) → vendored path (relative to the destination).
const FILE_RULES = [
  { match: (p) => p === 'client/lex-auth-client.ts', to: () => 'lex-auth-client.ts' },
  { match: (p) => p === 'VERSION', to: (p) => p },
  { match: (p) => p === 'README.md', to: (p) => p },
  { match: (p) => p.startsWith('fixtures/') && p.endsWith('.json'), to: (p) => p },
]

function parseArgs(argv) {
  const args = { check: false, dest: DEFAULT_DEST, from: null, ref: null }
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === '--check') args.check = true
    else if (arg === '--dest') args.dest = argv[++i]
    else if (arg === '--from') args.from = argv[++i]
    else if (arg === '--ref') args.ref = argv[++i]
    else if (arg === '--help' || arg === '-h') {
      console.log(
        fs
          .readFileSync(new URL(import.meta.url))
          .toString()
          .split('*/')[0],
      )
      process.exit(0)
    } else {
      console.error(`unknown argument: ${arg}`)
      process.exit(2)
    }
  }
  return args
}

const sha256 = (buffer) => createHash('sha256').update(buffer).digest('hex')

function toPosix(p) {
  return p.split(path.sep).join('/')
}

function walk(dir, base = dir) {
  if (!fs.existsSync(dir)) return []
  const out = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) out.push(...walk(full, base))
    else out.push(toPosix(path.relative(base, full)))
  }
  return out.sort()
}

/** Resolve `--from` to { repoRoot, contractDir } (repoRoot only when it is a lex-app checkout). */
function resolveSource(from) {
  const absolute = path.resolve(from)
  const asRepo = path.join(absolute, CONTRACT_DIR)
  if (fs.existsSync(asRepo)) return { repoRoot: absolute, contractDir: asRepo }
  if (fs.existsSync(path.join(absolute, 'client', 'lex-auth-client.ts'))) {
    // Walk up to the git root (if any) so --ref works with a contract-dir --from as well.
    let root = null
    try {
      root = execFileSync('git', ['-C', absolute, 'rev-parse', '--show-toplevel'], {
        encoding: 'utf8',
      }).trim()
    } catch {
      root = null
    }
    return { repoRoot: root, contractDir: absolute }
  }
  throw new Error(`--from ${from}: neither a lex-app checkout nor a contract directory`)
}

/** Map of vendored-relative path → Buffer, read from the source (working tree or git ref). */
function readSource({ repoRoot, contractDir }, ref) {
  const files = new Map()
  let sourcePaths
  let read
  if (ref) {
    if (!repoRoot) throw new Error('--ref needs --from to be inside a git checkout')
    const rel = toPosix(path.relative(repoRoot, contractDir))
    sourcePaths = execFileSync('git', ['-C', repoRoot, 'ls-tree', '-r', '--name-only', ref, rel], {
      encoding: 'utf8',
    })
      .split('\n')
      .filter(Boolean)
      .map((p) => p.slice(rel.length + 1))
    read = (p) => execFileSync('git', ['-C', repoRoot, 'show', `${ref}:${rel}/${p}`])
  } else {
    sourcePaths = walk(contractDir)
    read = (p) => fs.readFileSync(path.join(contractDir, p))
  }
  for (const p of sourcePaths) {
    const rule = FILE_RULES.find((r) => r.match(p))
    if (rule) files.set(rule.to(p), read(p))
  }
  if (!files.has('lex-auth-client.ts')) throw new Error('source has no client/lex-auth-client.ts')
  return files
}

function manifestFor(files, source) {
  const entries = [...files.entries()].sort(([a], [b]) => a.localeCompare(b))
  const hashes = Object.fromEntries(entries.map(([p, buf]) => [p, sha256(buf)]))
  const combined = sha256(Buffer.from(entries.map(([p]) => `${p}:${hashes[p]}`).join('\n')))
  const version = files.has('VERSION') ? files.get('VERSION').toString().trim() : 'unknown'
  return {
    contract: 'lex-auth/v2',
    version,
    source,
    sha256: combined,
    files: hashes,
    generated_by: 'scripts/vendor-lex-auth-client.mjs',
  }
}

function vendor(args) {
  const source = resolveSource(args.from)
  const files = readSource(source, args.ref)
  const dest = path.resolve(args.dest)
  fs.rmSync(dest, { recursive: true, force: true })
  for (const [rel, buf] of files) {
    const target = path.join(dest, rel)
    fs.mkdirSync(path.dirname(target), { recursive: true })
    fs.writeFileSync(target, buf)
  }
  const label = `${toPosix(path.relative(process.cwd(), source.contractDir)) || '.'}${args.ref ? `@${args.ref}` : ''}`
  const manifest = manifestFor(files, label)
  fs.writeFileSync(path.join(dest, 'VENDORED.json'), `${JSON.stringify(manifest, null, 2)}\n`)
  console.log(
    `vendored lex-auth contract ${manifest.version} (${files.size} files) into ${args.dest}`,
  )
}

function check(args) {
  const dest = path.resolve(args.dest)
  const manifestPath = path.join(dest, 'VENDORED.json')
  if (!fs.existsSync(manifestPath)) {
    console.error(`drift: ${args.dest}/VENDORED.json is missing`)
    return 1
  }
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
  const problems = []
  const present = walk(dest).filter((p) => p !== 'VENDORED.json')
  for (const p of present) {
    if (!(p in manifest.files)) problems.push(`extra file: ${p}`)
    else if (sha256(fs.readFileSync(path.join(dest, p))) !== manifest.files[p]) {
      problems.push(`edited by hand: ${p}`)
    }
  }
  for (const p of Object.keys(manifest.files)) {
    if (!present.includes(p)) problems.push(`missing file: ${p}`)
  }
  if (args.from) {
    const files = readSource(resolveSource(args.from), args.ref)
    const fresh = manifestFor(files, manifest.source)
    if (fresh.sha256 !== manifest.sha256) {
      problems.push(
        `differs from source ${args.from}${args.ref ? `@${args.ref}` : ''} — re-run the vendoring`,
      )
    }
  }
  if (problems.length > 0) {
    for (const problem of problems) console.error(`drift: ${problem}`)
    return 1
  }
  console.log(
    `lex-auth contract ${manifest.version}: vendored copy intact (${present.length} files)`,
  )
  return 0
}

const args = parseArgs(process.argv.slice(2))
try {
  if (args.check) process.exit(check(args))
  if (!args.from) {
    console.error('--from is required when vendoring')
    process.exit(2)
  }
  vendor(args)
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error))
  process.exit(1)
}
