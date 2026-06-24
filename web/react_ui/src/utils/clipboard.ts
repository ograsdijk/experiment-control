// Clipboard helper that works in both secure and insecure contexts.
//
// `navigator.clipboard` is only exposed by browsers in a "secure context"
// (HTTPS, or http://localhost / 127.0.0.1). The UI is often served over plain
// HTTP and reached via a LAN IP or hostname, where `navigator.clipboard` is
// undefined. In that case we fall back to a hidden <textarea> +
// document.execCommand("copy"), which still works without a secure context.

async function writeViaClipboardApi(text: string): Promise<boolean> {
  if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) {
    return false;
  }
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

function writeViaExecCommand(text: string): boolean {
  if (typeof document === "undefined") {
    return false;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  // Keep it out of view and out of the layout / scroll position.
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "0";
  textarea.style.left = "0";
  textarea.style.width = "1px";
  textarea.style.height = "1px";
  textarea.style.padding = "0";
  textarea.style.border = "none";
  textarea.style.outline = "none";
  textarea.style.boxShadow = "none";
  textarea.style.background = "transparent";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  const previousSelection = document.activeElement as HTMLElement | null;
  try {
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, text.length);
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    document.body.removeChild(textarea);
    // Restore focus to whatever the user was on.
    if (previousSelection && typeof previousSelection.focus === "function") {
      previousSelection.focus();
    }
  }
}

// Copy `text` to the clipboard, preferring the async Clipboard API and falling
// back to execCommand for insecure contexts. Returns true on success.
export async function copyToClipboard(text: string): Promise<boolean> {
  if (await writeViaClipboardApi(text)) {
    return true;
  }
  return writeViaExecCommand(text);
}
