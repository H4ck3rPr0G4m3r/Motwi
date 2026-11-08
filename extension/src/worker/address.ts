// Where a mod's Motwi is right now. Today that's only what the last link said. A "phonebook"
// that looks up the current address could answer here later, with nothing else changing.

export async function currentOrigin(pair: { origin: string }): Promise<string> {
  return pair.origin;
}
