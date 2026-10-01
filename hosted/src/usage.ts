// What hosting costs (W-860): this month's Cloudflare usage against the
// Workers Paid plan's allowances, and the bill it comes to. Account-wide, read
// from the GraphQL Analytics API with a read-only token (CF_API_TOKEN, a
// secret: Account Analytics Read). Containers are billed as Cloudflare
// measures them (W-915): memory and disk for the time an instance runs, CPU
// for the time it is busy. Without the token, the front doors' own count
// (Household.addUsage) stands in, as a basic instance busy all the time it
// runs. Cloudflare's Billable Usage page is the bill.

import type { Env } from "./index";

export type Meter = {
  group: string; label: string; unit: string;
  used: number | null;       // null: could not be read
  included: number;
  price: number;             // dollars per unit past the allowance
};

/** One day's use priced past the allowance, in dollars, containers apart
 * (W-923). The month's allowances cover the first of it, so this is a day's
 * cost at the margin, not what the bill grew by that day. */
export type SpendDay = { day: string; containers: number; other: number };

export type Usage = { meters: Meter[]; bill: number; projected: number; month: string; live: boolean;
                      measured: boolean;   // the Containers meters are Cloudflare's, not our estimate
                      days: SpendDay[] };

/** One household's server time on one UTC day (the front door's count). */
export type UsageDay = { day: string; wakes: number; server_ms: number; wake_ms: number; page_ms: number };

/** A household's server time over its last days, wakes apart from pages
 * (W-907). A day from before the split has only its total, so it is left
 * out, and `since` names the first day counted (today, if none is yet)
 * while one is in the window. */
export function serverTime(days: UsageDay[], today = new Date().toISOString().slice(0, 10)):
    { wakes: number; wake_ms: number; page_ms: number; since: string | null } {
  const split = days.filter((d) => d.wake_ms + d.page_ms > 0 || !d.server_ms);
  const since = split.length === days.length ? null
    : split.reduce((a, d) => (d.day < a ? d.day : a), today);
  return {
    wakes: split.reduce((a, d) => a + d.wakes, 0),
    wake_ms: split.reduce((a, d) => a + d.wake_ms, 0),
    page_ms: split.reduce((a, d) => a + d.page_ms, 0),
    since,
  };
}

const PLAN = 5;              // Workers Paid, a month
// A "basic" container: 1/4 vCPU, 1 GiB memory, 4 GB disk.
const BASIC = { vcpu: 0.25, gib: 1, disk: 4 };
const DO_GB = 0.128;         // a Durable Object is billed as 128 MB while active
const GB = 1e9;
const GIB = 2 ** 30;
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
  return measuredContainerMeters({ cpuS: s * BASIC.vcpu, memByteS: s * BASIC.gib * GIB,
                                   diskByteS: s * BASIC.disk * GB });
}

/** Containers as Cloudflare measures them: busy vCPU-seconds, and the
 * byte-seconds of memory and disk the running instances held. */
export function measuredContainerMeters(u: { cpuS: number; memByteS: number; diskByteS: number }): Meter[] {
  return [
    { group: "Containers", label: "CPU", unit: "vCPU-min", used: u.cpuS / 60, included: 375, price: 0.00002 * 60 },
    { group: "Containers", label: "Memory", unit: "GiB-h", used: u.memByteS / GIB / 3600, included: 25, price: 0.0000025 * 3600 },
    { group: "Containers", label: "Disk", unit: "GB-h", used: u.diskByteS / GB / 3600, included: 200, price: 0.00000007 * 3600 },
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

const VAR_TYPES: Record<string, string> = { s: "Time", e: "Time", m: "Time", d0: "Date", d1: "Date", recent: "Date" };

const sum = (rows: any[], f: (r: any) => number) => rows.reduce((a, r) => a + (Number(f(r)) || 0), 0);

/** One flow's amount by UTC day. */
type Daily = Map<string, number>;

function byDay(rows: any[], f: (r: any) => number): Daily {
  const out: Daily = new Map();
  for (const r of rows) out.set(r.dimensions.date, (out.get(r.dimensions.date) ?? 0) + (Number(f(r)) || 0));
  return out;
}

/** What is used up as it goes, by day; null where a dataset could not be read. */
export type Flows = {
  wReq: Daily | null; wCpu: Daily | null; doReq: Daily | null; doActive: Daily | null;
  d1Read: Daily | null; d1Written: Daily | null; r2A: Daily | null; r2B: Daily | null;
  containers: { cpuS: Daily; memByteS: Daily; diskByteS: Daily } | null;
};

/** The flows' meters, each amount read through `pick`: a month's total, or one day's. */
function flowMeters(f: Flows, pick: (d: Daily | null) => number | null): Meter[] {
  return [
    { group: "Workers", label: "Requests", unit: "", used: pick(f.wReq), included: 10 * M, price: 0.30 / M },
    { group: "Workers", label: "CPU", unit: "ms", used: pick(f.wCpu), included: 30 * M, price: 0.02 / M },
    { group: "Durable Objects", label: "Requests", unit: "", used: pick(f.doReq), included: 1 * M, price: 0.15 / M },
    { group: "Durable Objects", label: "Duration", unit: "GB-s", used: pick(f.doActive), included: 400000, price: 12.5 / M },
    { group: "D1", label: "Rows read", unit: "", used: pick(f.d1Read), included: 25000 * M, price: 0.001 / M },
    { group: "D1", label: "Rows written", unit: "", used: pick(f.d1Written), included: 50 * M, price: 1 / M },
    { group: "R2", label: "Class A", unit: "", used: pick(f.r2A), included: 1 * M, price: 4.5 / M },
    { group: "R2", label: "Class B", unit: "", used: pick(f.r2B), included: 10 * M, price: 0.36 / M },
    ...(f.containers ? measuredContainerMeters({ cpuS: pick(f.containers.cpuS) ?? 0,
      memByteS: pick(f.containers.memByteS) ?? 0, diskByteS: pick(f.containers.diskByteS) ?? 0 }) : []),
  ];
}

/** Each day's use at the price past the allowance (W-923). Only once the
 * containers' own dataset is read: they are most of the bill, and the front
 * doors' count is not kept by day across households here. */
export function spendByDay(f: Flows, days: string[]): SpendDay[] {
  if (!f.containers) return [];
  return days.map((day) => {
    const cost = (m: Meter) => (m.used ?? 0) * m.price;
    const meters = flowMeters(f, (d) => d?.get(day) ?? 0);
    return { day,
      containers: meters.filter((m) => m.group === "Containers").reduce((a, m) => a + cost(m), 0),
      other: meters.filter((m) => m.group !== "Containers").reduce((a, m) => a + cost(m), 0) };
  });
}

const GROUPS = ["Workers", "Durable Objects", "D1", "R2", "Containers"];
const DAY = 86400e3;

/** The last `n` UTC days, oldest first, ending today. */
export function lastDays(n: number, now = new Date()): string[] {
  const end = Math.floor(now.getTime() / DAY) * DAY;
  return Array.from({ length: n }, (_, i) => new Date(end - (n - 1 - i) * DAY).toISOString().slice(0, 10));
}

export async function cloudflareUsage(env: Env, serverMs: number, now = new Date()): Promise<Usage> {
  const month = now.toISOString().slice(0, 7);
  const containers = containerMeters(serverMs);
  if (!env.CF_API_TOKEN || !env.CF_ACCOUNT_ID) {
    return { meters: containers, bill: bill(containers), projected: bill(project(containers, now)), month,
             live: false, measured: false, days: [] };
  }
  const start = `${month}-01`;
  const today = now.toISOString().slice(0, 10);
  // The flows are read by day for the last 30 days, which takes in the month
  // so far; the month's meters sum its own days.
  const days = lastDays(30, now);
  const from = days[0] < start ? days[0] : start;
  const recent = new Date(now.getTime() - 2 * DAY).toISOString().slice(0, 10);
  const vars = { a: env.CF_ACCOUNT_ID, s: `${from}T00:00:00Z`, e: now.toISOString(), m: `${start}T00:00:00Z`,
                 d0: from, d1: today, recent };
  const T = "filter: { datetime_geq: $s, datetime_leq: $e }";
  const D = "filter: { date_geq: $d0, date_leq: $d1 }";
  const BY = "dimensions { date }";

  const [wReq, wCpu, doReq, doActive, doStored, d1Rows, d1Stored, r2Ops, r2Stored, measured] = await Promise.all([
    ask(env, "workers requests", `workersInvocationsAdaptive(limit: 10000, ${T}) { sum { requests } ${BY} }`, vars,
      (a) => byDay(a.workersInvocationsAdaptive, (r) => r.sum.requests)),
    ask(env, "workers cpu", `workersInvocationsAdaptive(limit: 10000, ${T}) { sum { cpuTimeUs } ${BY} }`, vars,
      (a) => byDay(a.workersInvocationsAdaptive, (r) => r.sum.cpuTimeUs / 1000)),
    ask(env, "do requests", `durableObjectsInvocationsAdaptiveGroups(limit: 10000, ${D}) { sum { requests } ${BY} }`, vars,
      (a) => byDay(a.durableObjectsInvocationsAdaptiveGroups, (r) => r.sum.requests)),
    ask(env, "do duration", `durableObjectsPeriodicGroups(limit: 10000, ${D}) { sum { activeTime } ${BY} }`, vars,
      (a) => byDay(a.durableObjectsPeriodicGroups, (r) => r.sum.activeTime / 1e6 * DO_GB)),
    ask(env, "do storage", `durableObjectsStorageGroups(limit: 10, filter: { date_geq: $recent }) { max { storedBytes } }`, vars,
      (a) => Math.max(0, ...a.durableObjectsStorageGroups.map((r: any) => Number(r.max.storedBytes) || 0)) / GB),
    ask(env, "d1 rows", `d1AnalyticsAdaptiveGroups(limit: 10000, ${D}) { sum { rowsRead rowsWritten } ${BY} }`, vars,
      (a) => ({ read: byDay(a.d1AnalyticsAdaptiveGroups, (r) => r.sum.rowsRead),
                written: byDay(a.d1AnalyticsAdaptiveGroups, (r) => r.sum.rowsWritten) })),
    ask(env, "d1 storage", `d1StorageAdaptiveGroups(limit: 100, filter: { date_geq: $recent }) { max { databaseSizeBytes } dimensions { databaseId } }`, vars,
      (a) => sum(a.d1StorageAdaptiveGroups, (r) => r.max.databaseSizeBytes) / GB),
    ask(env, "r2 operations", `r2OperationsAdaptiveGroups(limit: 10000, ${T}) { sum { requests } dimensions { actionType date } }`, vars,
      (a) => {
        const rows = a.r2OperationsAdaptiveGroups.filter((r: any) => !R2_FREE.has(r.dimensions.actionType));
        return { classA: byDay(rows.filter((r: any) => R2_CLASS_A.has(r.dimensions.actionType)), (r) => r.sum.requests),
                 classB: byDay(rows.filter((r: any) => !R2_CLASS_A.has(r.dimensions.actionType)), (r) => r.sum.requests) };
      }),
    ask(env, "r2 storage", `r2StorageAdaptiveGroups(limit: 100, filter: { datetime_geq: $m, datetime_leq: $e }) { max { payloadSize metadataSize } dimensions { bucketName } }`, vars,
      (a) => sum(a.r2StorageAdaptiveGroups, (r) => Number(r.max.payloadSize) + Number(r.max.metadataSize)) / GB),
    ask(env, "containers", `containersUsageAdaptiveGroups(limit: 10000, ${D}) { sum { cpuTimeSec allocatedMemory allocatedDisk } ${BY} }`, vars,
      (a) => ({ cpuS: byDay(a.containersUsageAdaptiveGroups, (r) => r.sum.cpuTimeSec),
                memByteS: byDay(a.containersUsageAdaptiveGroups, (r) => r.sum.allocatedMemory),
                diskByteS: byDay(a.containersUsageAdaptiveGroups, (r) => r.sum.allocatedDisk) })),
  ]);

  const flows: Flows = { wReq, wCpu, doReq, doActive, d1Read: d1Rows?.read ?? null, d1Written: d1Rows?.written ?? null,
                         r2A: r2Ops?.classA ?? null, r2B: r2Ops?.classB ?? null, containers: measured };
  const thisMonth = (d: Daily | null) => d && [...d].reduce((a, [day, v]) => (day >= start ? a + v : a), 0);
  const meters: Meter[] = [
    ...flowMeters(flows, thisMonth),
    ...(measured ? [] : containers),
    { group: "Durable Objects", label: "Storage", unit: "GB", used: doStored, included: 5, price: 0.20 },
    { group: "D1", label: "Storage", unit: "GB", used: d1Stored, included: 5, price: 0.75 },
    { group: "R2", label: "Storage", unit: "GB", used: r2Stored, included: 10, price: 0.015 },
  ].sort((a, b) => GROUPS.indexOf(a.group) - GROUPS.indexOf(b.group));
  return { meters, bill: bill(meters), projected: bill(project(meters, now)), month, live: true,
           measured: measured !== null, days: spendByDay(flows, days) };
}
