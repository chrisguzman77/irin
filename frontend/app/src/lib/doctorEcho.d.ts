// Types for doctorEcho.js, a byte-for-byte copy of frontend/display/doctor-echo.js
// (one echo wording for the kiosk and the app). Importing it defines
// globalThis.irinDoctorEcho; use echoDoctorMessage from lib/doctorMessages.ts.
export {};
declare global {
  var irinDoctorEcho: (
    m: unknown,
    doctorName: string | null,
  ) => { who: string; line: string; note: string | null; insulin: string | null; confirm: string; known: boolean };
}
