// Robust clipboard copy that also works over plain HTTP.
//
// navigator.clipboard exists ONLY in a "secure context" (HTTPS or localhost).
// The admin is served over http://<ip>:3001, so navigator.clipboard is
// undefined there and writeText() throws — making copy buttons silently fail.
// We try the modern API first, then fall back to the legacy execCommand('copy')
// via a hidden <textarea>, which works in insecure contexts.
export async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) {
    // fall through to the legacy path
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-9999px';
    ta.style.left = '-9999px';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, text.length);
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch (_) {
    return false;
  }
}
