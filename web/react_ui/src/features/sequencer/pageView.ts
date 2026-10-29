// `?view=sequencer` opens the dashboard as a page that shows only the
// Sequencer, for a tab or window of its own. The dashboard behind the modal
// then does not render at all.
const VIEW_PARAM = "view";
const SEQUENCER_VIEW = "sequencer";

export function isSequencerPageView(search: string = window.location.search): boolean {
  return new URLSearchParams(search).get(VIEW_PARAM) === SEQUENCER_VIEW;
}

/** This dashboard's URL as the Sequencer page, keeping its other parameters. */
export function sequencerPageUrl(href: string = window.location.href): string {
  const url = new URL(href);
  url.searchParams.set(VIEW_PARAM, SEQUENCER_VIEW);
  url.hash = "";
  return url.toString();
}
