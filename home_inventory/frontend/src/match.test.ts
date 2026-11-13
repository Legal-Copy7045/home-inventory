import { describe, expect, it } from "vitest";
import { matchesWords } from "./match";
import shared from "./match_cases.json";

const cases = shared as unknown as { match: [string, string][]; no_match: [string, string][] };

describe("matchesWords (same cases as the server's textmatch)", () => {
  it.each(cases.match)("%s finds %s", (q, text) => expect(matchesWords(q, text)).toBe(true));
  it.each(cases.no_match)("%s does not find %s", (q, text) => expect(matchesWords(q, text)).toBe(false));
});
