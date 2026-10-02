// @ts-nocheck: node:sqlite has no types under the Worker's tsconfig.
// The Durable Object's ctx.storage.sql, on node's own SQLite, for tests.
import { DatabaseSync } from "node:sqlite";

export function nodeSql(db = new DatabaseSync(":memory:")) {
  const empty = (columnNames: string[] = []) => ({ toArray: () => [], one: () => undefined, columnNames, rowsWritten: 0 });
  return {
    db,
    exec(q: string, ...a: unknown[]) {
      const probe = /^SELECT \* FROM (\w+) LIMIT 0$/.exec(q.trim());
      if (probe) return empty(db.prepare(`PRAGMA table_info(${probe[1]})`).all().map((c) => c.name));
      if (!a.length && q.split(";").filter((s) => s.trim()).length > 1) { db.exec(q); return empty(); }
      const st = db.prepare(q);
      let rowsWritten = 0;
      const rows = /^\s*(SELECT|PRAGMA|WITH)/i.test(q) ? st.all(...a) : (rowsWritten = Number(st.run(...a).changes), []);
      return { toArray: () => rows, one: () => rows[0], columnNames: rows[0] ? Object.keys(rows[0]) : [], rowsWritten };
    },
  };
}
