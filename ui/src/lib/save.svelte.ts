// The one save model (spec 6.2): a change saves when committed, and each field
// shows its own Saving / Saved / error state.

import { ApiError, message } from "./api";

export type FieldState = { state: "saving" | "saved" | "error"; message?: string };

export function createSaver() {
  const fields = $state<Record<string, FieldState>>({});
  const timers: Record<string, ReturnType<typeof setTimeout>> = {};

  async function save<T>(field: string, run: () => Promise<T>): Promise<T | undefined> {
    clearTimeout(timers[field]);
    fields[field] = { state: "saving" };
    try {
      const result = await run();
      fields[field] = { state: "saved" };
      timers[field] = setTimeout(() => delete fields[field], 2200);
      return result;
    } catch (e) {
      const target = e instanceof ApiError && e.field ? e.field : field;
      fields[field] = { state: "error", message: message(e) };
      if (target !== field) fields[target] = { state: "error", message: message(e) };
      return undefined;
    }
  }

  function clear(field: string) {
    delete fields[field];
  }

  return { fields, save, clear };
}
