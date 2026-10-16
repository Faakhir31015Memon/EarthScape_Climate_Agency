import { Prose } from "./PublicLayout";

export function PrivacyPage() {
  return (
    <Prose>
      <h1>Privacy</h1>
      <p>
        EarthScape Climate Agency is an academic demonstration platform. This page describes what the
        system handles.
      </p>
      <h2>Account data</h2>
      <p>
        We store your name, email address, role, and authentication metadata (session records and a
        password-verification secret). We never store your password in plaintext. Access tokens are
        held in memory only; a short-lived refresh token is stored in your browser so a reload can
        restore your session.
      </p>
      <h2>Climate data</h2>
      <p>
        Observations are ingested from public provider APIs (Open-Meteo, NASA POWER). These are
        environmental measurements and contain no personal data.
      </p>
      <h2>No tracking</h2>
      <p>This deployment does not run advertising or third-party analytics trackers.</p>
      <h2>Contact</h2>
      <p>For questions about this demonstration, contact the project administrator.</p>
    </Prose>
  );
}
