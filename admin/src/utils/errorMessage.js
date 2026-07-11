// Normalizes an Axios/FastAPI error into a plain string safe to render in React.
//
// FastAPI returns `detail` as a STRING for HTTPException (e.g. 401 "Incorrect
// email or password") but as an ARRAY of objects for 422 validation errors
// (e.g. a malformed email: [{ loc, msg, type, input }]). Passing that array/
// object straight into setError() and rendering it crashes with React error
// #31 ("Objects are not valid as a React child") — the login/forms go blank.
// This helper flattens every shape to a readable string.
export function getErrorMessage(err, fallback = 'Something went wrong') {
  const detail = err?.response?.data?.detail;

  if (typeof detail === 'string') return detail;

  // 422 validation error: array of { msg, loc, ... }
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (typeof d === 'string' ? d : d?.msg))
      .filter(Boolean);
    if (msgs.length) return msgs.join('. ');
  }

  // Single object with a msg field
  if (detail && typeof detail === 'object' && typeof detail.msg === 'string') {
    return detail.msg;
  }

  // Other common shapes
  if (typeof err?.response?.data?.message === 'string') return err.response.data.message;
  if (typeof err?.message === 'string' && err.message) return err.message;

  return fallback;
}

export default getErrorMessage;
