import { describe, expect, it } from "vitest";
import { editorMatchesLoadedRevision, revisionAction } from "./loaded_revision";

describe("editorMatchesLoadedRevision", () => {
  it("matches only on equal revisions", () => {
    expect(editorMatchesLoadedRevision({ statusRevision: 3, editorRevision: 3 })).toBe(true);
    expect(editorMatchesLoadedRevision({ statusRevision: 4, editorRevision: 3 })).toBe(false);
    expect(editorMatchesLoadedRevision({ statusRevision: 3, editorRevision: null })).toBe(false);
  });

  it("keeps the old behaviour without a status revision", () => {
    expect(editorMatchesLoadedRevision({ statusRevision: null, editorRevision: null })).toBe(true);
    expect(editorMatchesLoadedRevision({ statusRevision: null, editorRevision: 2 })).toBe(true);
  });
});

describe("revisionAction", () => {
  const base = { statusRevision: 4, editorRevision: 3, loaded: true, dirty: false };

  it("refetches into a clean editor", () => {
    expect(revisionAction(base)).toBe("refetch");
    expect(revisionAction({ ...base, editorRevision: null })).toBe("refetch");
  });

  it("only notifies for a dirty editor", () => {
    expect(revisionAction({ ...base, dirty: true })).toBe("notice");
  });

  it("does nothing when in sync, nothing is loaded, or the backend is old", () => {
    expect(revisionAction({ ...base, editorRevision: 4 })).toBe("none");
    expect(revisionAction({ ...base, dirty: true, editorRevision: 4 })).toBe("none");
    expect(revisionAction({ ...base, loaded: false })).toBe("none");
    expect(revisionAction({ ...base, statusRevision: null })).toBe("none");
  });
});
