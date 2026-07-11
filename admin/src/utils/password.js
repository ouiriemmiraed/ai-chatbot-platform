// Client-side password policy — kept in sync with the backend's
// validate_password_strength() so users get immediate, clear feedback instead
// of submitting and hitting a raw 400. Rules: 8+ chars, at least one uppercase,
// one lowercase, one digit and one special character.
export const isStrongPassword = (pw) =>
    !!pw &&
    pw.length >= 8 &&
    /[A-Z]/.test(pw) &&
    /[a-z]/.test(pw) &&
    /[0-9]/.test(pw) &&
    /[^A-Za-z0-9]/.test(pw);
