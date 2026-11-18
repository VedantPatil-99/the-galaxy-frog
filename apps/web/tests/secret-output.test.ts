import { expect, test } from "bun:test"
import { mkdtempSync, rmdirSync, unlinkSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"

const repository = resolve(import.meta.dir, "../../..")
const secret = "synthetic-credential-must-never-reach-output"

for (const status of [200, 503]) {
  test(`shell smoke hides raw bodies for HTTP ${status}`, async () => {
    const server = Bun.serve({
      hostname: "127.0.0.1",
      port: 0,
      fetch: () => new Response(secret, { status }),
    })
    try {
      const child = Bun.spawn({
        cmd: [
          process.platform === "win32" ? "C:/Program Files/Git/bin/bash.exe" : "bash",
          join(repository, "scripts/smoke-test.sh"),
        ],
        env: {
          PATH: process.env.PATH ?? "",
          WEB_BASE_URL: `http://127.0.0.1:${server.port}`,
        },
        stdout: "pipe",
        stderr: "pipe",
      })
      const [exitCode, stdout, stderr] = await Promise.all([
        child.exited,
        new Response(child.stdout).text(),
        new Response(child.stderr).text(),
      ])
      expect(exitCode).not.toBe(0)
      expect(stdout + stderr).not.toContain(secret)
      expect(stderr).toContain(status === 503 ? "received 503" : "is missing")
    } finally {
      server.stop(true)
    }
  })
}

for (const phase of [1, 2]) {
  for (const failure of ["http", "json", "network"]) {
    test(`phase ${phase} smoke hides ${failure} diagnostics`, () => {
      const directory = mkdtempSync(join(tmpdir(), "galaxy-frog-secret-test-"))
      const preload = join(directory, "mock-fetch.mjs")
      writeFileSync(preload, `
        globalThis.fetch = async () => {
          if (${JSON.stringify(failure)} === "network") {
            throw new Error(${JSON.stringify(secret)});
          }
          return new Response(${JSON.stringify(secret)}, {
            status: ${failure === "http" ? 503 : 200}
          });
        };
      `)
      try {
        const result = Bun.spawnSync({
          cmd: [process.execPath, "--preload", preload, join(repository, `scripts/phase-${phase}-smoke.ts`)],
          env: {
            PATH: process.env.PATH ?? "",
            PHASE1_VIDEO_URL: "https://youtu.be/dQw4w9WgXcQ",
            PHASE1_QUESTION: "What is discussed?",
            PHASE2_VIDEO_URL: "https://youtu.be/dQw4w9WgXcQ",
          },
          stdout: "pipe",
          stderr: "pipe",
        })
        const output = new TextDecoder().decode(result.stdout) + new TextDecoder().decode(result.stderr)
        expect(result.exitCode).not.toBe(0)
        expect(output).not.toContain(secret)
        expect(output).toContain(failure === "http" ? "HTTP 503" : failure === "json" ? "invalid JSON" : "request failed")
      } finally {
        unlinkSync(preload)
        rmdirSync(directory)
      }
    })
  }
}
