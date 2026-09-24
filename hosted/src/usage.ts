// What hosting costs (W-860): this month's Cloudflare usage against the
// Workers Paid plan's allowances, and the bill it comes to. Account-wide, read
// from the GraphQL Analytics API with a read-only token (CF_API_TOKEN, a
// secret: Account Analytics Read). Containers are not in that API, so their
// time is the front doors' own count (Household.addUsage), which is rough:
// the Lobby's time is not in it. Cloudflare's Billable Usage page is the bill.

import type { Env } from "./index";

export type Meter = {
  group: string; label: string; unit: string;
  used: number | null;       // null: could not be read
  included: number;
  price: number;             // dollars per unit past the allowance
};

export type Usage = { meters: Meter[]; bill: number; projected: number; month: string; live: boolean };

const PLAN = 5;              // Workers Paid, a month
// A "basic" container: 1/4 vCPU, 1 GiB memory, 4 GB disk.
const BASIC = { vcpu: 0.25, gib: 1, disk: 4 };
const DO_GB = 0.128;         // a Durable Object is billed as 128 MB while active
const GB = 1e9;
const M = 1e6;

const R2_CLASS_A = new Set(["ListBuckets", "PutBucket", "ListObjects", "PutObject", "CopyObject",
  "CompleteMultipartUpload", "CreateMultipartUpload", "LifecycleStorageTierTransition",
  "ListMultipartUploads", "UploadPart", "UploadPartCopy", "ListParts", "PutBucketEncryption",
  "PutBucketCors", "PutBucketLifecycleConfiguration"]);
const R2_FREE = new Set(["DeleteObject", "DeleteBucket", "AbortMultipartUpload"]);

/** The bill for these meters: the plan, plus whatever is past an allowance. */
export function bill(meters: Meter[]): number {
  return PLAN + meters.reduce((a, m) => a + Math.max(0, (m.used ?? 0) - m.included) * m.price, 0);
}

/** Each meter carried on at this month's pace to its end. Storage is a level,
 * not a flow, so it stays where it is. */
export function project(meters: Meter[], now = new Date()): Meter[] {
  const start = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1);
  const end = Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1);
  const pace = (end - start) / Math.max(now.getTime() - start, 3600e3);
  return meters.map((m) => m.unit === "GB" || m.used === null ? m : { ...m, used: m.used * pace });
}

/** Container time from the front doors' counts, this month so far. */
export function containerMeters(serverMs: number): Meter[] {
  const s = serverMs / 1000;
  return [
    { group: "Containers", label: "CPU", unit: "vCPU-min", used: s * BASIC.vcpu / 60, included: 375, price: 0.00002 * 60 },
    { group: "Containers", label: "Memory", unit: "GiB-h", used: s * BASIC.gib / 3600, included: 25, price: 0.0000025 * 3600 },
    { group: "Containers", label: "Disk", unit: "GB-h", used: s * BASIC.disk / 3600, included: 200, price: 0.00000007 * 3600 },
  ];
}

async function gql(env: Env, query: string, variables: Record<string, unknown>): Promise<any> {
  const r = await fetch("https://api.cloudflare.com/client/v4/graphql", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.CF_API_TOKEN}`, "Content-Type": "application/json" },
    body: JSON.stringify({ query, variables }),
  });
  const body = await r.json<{ data?: any; errors?: { message: string }[] | null }>();
  if (!r.ok || body.errors?.length || !body.data) throw new Error(body.errors?.[0]?.message || `graphql ${r.status}`);
  return body.data.viewer.accounts[0];
}

/** One dataset's answer, or null when it cannot be read: each is asked on its
 * own, so one field the API does not have costs one meter, not the card. */
async function ask<T>(env: Env, what: string, body: string, vars: Record<string, unknown>,
                      read: (a: any) => T): Promise<T | null> {
  try {
    // GraphQL refuses a variable declared and not used: declare only this body's.
    const decl = ["$a: string!", ...Object.entries(VAR_TYPES)
      .filter(([k]) => new RegExp(`\\$${k}\\b`).test(body)).map(([k, t]) => `$${k}: ${t}`)];
    return read(await gql(env, `query (${decl.join(", ")}) {
      viewer { accounts(filter: { accountTag: $a }) { ${body} } } }`, vars));
  } catch (err) {
    console.warn("usage", what, String(err));
    return null;
  }
}

const VAR_TYPES: Record<string, string> = { s: "Time", e: "Time", d0: "Date", d1: "Date", recent: "Date" };

const sum = (rows: any[], f: (r: any) => number) => rows.reduce((a, r) => a + (Number(f(r)) || 0), 0);

export async function cloudflareUsage(env: Env, serverMs: number, now = new Date()): Promise<Usage> {
  const month = now.toISOString().slice(0, 7);
  const containers = containerMeters(serverMs);
  if (!env.CF_API_TOKEN || !env.CF_ACCOUNT_ID) {
    return { meters: containers, bill: bill(containers), projected: bill(project(containers, now)), month, live: false };
  }
  const start = `${month}-01`;
  const today = now.toISOString().slice(0, 10);
  const recent = new Date(now.getTime() - 2 * 86400e3).toISOString().slice(0, 10);
  const vars = { a: env.CF_ACCOUNT_ID, s: `${start}T00:00:00Z`, e: now.toISOString(), d0: start, d1: today, recent };
  const T = "filter: { datetime_geq: $s, datetime_leq: $e }";
  const D = "filter: { date_geq: $d0, date_leq: $d1 }";

  const [wReq, wCpu, doReq, doActive, doStored, d1Rows, d1Stored, r2Ops, r2Stored] = await Promise.all([
    ask(env, "workers requests", `workersInvocationsAdaptive(limit: 10000, ${T}) { sum { requests } }`, vars,
      (a) => sum(a.workersInvocationsAdaptive, (r) => r.sum.requests)),
    ask(env, "workers cpu", `workersInvocationsAdaptive(limit: 10000, ${T}) { sum { cpuTimeUs } }`, vars,
      (a) => sum(a.workersInvocationsAdaptive, (r) => r.sum.cpuTimeUs) / 1000),
    ask(env, "do requests", `durableObjectsInvocationsAdaptiveGroups(limit: 10000, ${D}) { sum { requests } }`, vars,
      (a) => sum(a.durableObjectsInvocationsAdaptiveGroups, (r) => r.sum.requests)),
    ask(env, "do duration", `durableObjectsPeriodicGroups(limit: 10000, ${D}) { sum { activeTime } }`, vars,
      (a) => sum(a.durableObjectsPeriodicGroups, (r) => r.sum.activeTime) / 1e6 * DO_GB),
    ask(env, "do storage", `durableObjectsStorageGroups(limit: 10, filter: { date_geq: $recent }) { max { storedBytes } }`, vars,
      (a) => Math.max(0, ...a.durableObjectsStorageGroups.map((r: any) => Number(r.max.storedBytes) || 0)) / GB),
    ask(env, "d1 rows", `d1AnalyticsAdaptiveGroups(limit: 10000, ${D}) { sum { rowsRead rowsWritten } }`, vars,
      (a) => ({ read: sum(a.d1AnalyticsAdaptiveGroups, (r) => r.sum.rowsRead),
                written: sum(a.d1AnalyticsAdaptiveGroups, (r) => r.sum.rowsWritten) })),
    ask(env, "d1 storage", `d1StorageAdaptiveGroups(limit: 100, filter: { date_geq: $recent }) { max { databaseSizeBytes } dimensions { databaseId } }`, vars,
      (a) => sum(a.d1StorageAdaptiveGroups, (r) => r.max.databaseSizeBytes) / GB),
    ask(env, "r2 operations", `r2OperationsAdaptiveGroups(limit: 10000, ${T}) { sum { requests } dimensions { actionType } }`, vars,
      (a) => {
        let classA = 0, classB = 0;
        for (const r of a.r2OperationsAdaptiveGroups) {
          const t = r.dimensions.actionType;
          if (R2_FREE.has(t)) continue;
          if (R2_CLASS_A.has(t)) classA += r.sum.requests; else classB += r.sum.requests;
        }
        return { classA, classB };
      }),
    ask(env, "r2 storage", `r2StorageAdaptiveGroups(limit: 100, filter: { datetime_geq: $s, datetime_leq: $e }) { max { payloadSize metadataSize } dimensions { bucketName } }`, vars,
      (a) => sum(a.r2StorageAdaptiveGroups, (r) => Number(r.max.payloadSize) + Number(r.max.metadataSize)) / GB),
  ]);

  const meters: Meter[] = [
    { group: "Workers", label: "Requests", unit: "", used: wReq, included: 10 * M, price: 0.30 / M },
    { group: "Workers", label: "CPU", unit: "ms", used: wCpu, included: 30 * M, price: 0.02 / M },
    { group: "Durable Objects", label: "Requests", unit: "", used: doReq, included: 1 * M, price: 0.15 / M },
    { group: "Durable Objects", label: "Duration", unit: "GB-s", used: doActive, included: 400000, price: 12.5 / M },
    { group: "Durable Objects", label: "Storage", unit: "GB", used: doStored, included: 5, price: 0.20 },
    { group: "D1", label: "Rows read", unit: "", used: d1Rows?.read ?? null, included: 25000 * M, price: 0.001 / M },
    { group: "D1", label: "Rows written", unit: "", used: d1Rows?.written ?? null, included: 50 * M, price: 1 / M },
    { group: "D1", label: "Storage", unit: "GB", used: d1Stored, included: 5, price: 0.75 },
    { group: "R2", label: "Class A", unit: "", used: r2Ops?.classA ?? null, included: 1 * M, price: 4.5 / M },
    { group: "R2", label: "Class B", unit: "", used: r2Ops?.classB ?? null, included: 10 * M, price: 0.36 / M },
    { group: "R2", label: "Storage", unit: "GB", used: r2Stored, included: 10, price: 0.015 },
    ...containers,
  ];
  return { meters, bill: bill(meters), projected: bill(project(meters, now)), month, live: true };
}
