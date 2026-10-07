/**
 * Every key of `T`, and only those: a key of the type left out of the list
 * does not compile, so the list a method checks at run time cannot drift from
 * the type its callers see.
 */
export function keysOf<T>() {
  return <K extends readonly (keyof T & string)[]>(
    ...keys: K & (Exclude<keyof T, K[number]> extends never
      ? unknown
      : ["missing keys:", Exclude<keyof T, K[number]>])
  ): readonly string[] => keys;
}

/**
 * Refuse an option the method does not read (2026-10-07). In plain JavaScript
 * `search("x", { userId: "u1" })` dropped `userId` and searched every end user
 * of the project, `add("x", { userId: "u1" })` wrote a memory with no end user
 * (so `deleteAll({ user_id: "u1" })` never found it), and
 * `getFacts({ asOf: "2020-01-01" })` answered with today's facts: found by
 * window A's new-customer test. The REST API answers 422 to an unknown field
 * and the Python client raises TypeError; this client now refuses it too,
 * naming the spelling it takes.
 *
 * The message, or undefined when every key is known: each entry point throws
 * its own KorelyError (the AI SDK entry takes it from "korely-memory", so a
 * caller's `instanceof KorelyError` holds whichever entry threw).
 */
export function unknownOption(method: string, opts: unknown, known: readonly string[]): string | undefined {
  if (opts === undefined || opts === null) return undefined;
  if (typeof opts !== "object" || Array.isArray(opts)) {
    return `${method}: the options must be an object, got ${Array.isArray(opts) ? "an array" : typeof opts}.`;
  }
  for (const key of Object.keys(opts)) {
    if (known.includes(key)) continue;
    const snake = key.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
    const camel = key.replace(/_([a-z])/g, (_m, c: string) => c.toUpperCase());
    const meant = known.includes(snake) ? snake : known.includes(camel) ? camel : undefined;
    return (
      `${method}: unknown option ${key}.` +
      (meant ? ` Did you mean ${meant}?` : "") +
      ` It takes: ${known.join(", ")}.`
    );
  }
  return undefined;
}
