import { describe, expect, it } from "vitest";
import { isSequencerPageView, sequencerPageUrl } from "./pageView";

describe("sequencer page view", () => {
  it("is chosen by ?view=sequencer only", () => {
    expect(isSequencerPageView("?view=sequencer")).toBe(true);
    expect(isSequencerPageView("?perf=1&view=sequencer")).toBe(true);
    expect(isSequencerPageView("")).toBe(false);
    expect(isSequencerPageView("?view=devices")).toBe(false);
  });

  it("links to this dashboard as the page, keeping other parameters", () => {
    const url = new URL(sequencerPageUrl("http://lab-pc:8000/ui/?perf=1#panel"));
    expect(url.pathname).toBe("/ui/");
    expect(url.searchParams.get("perf")).toBe("1");
    expect(url.hash).toBe("");
    expect(isSequencerPageView(url.search)).toBe(true);
  });
});
