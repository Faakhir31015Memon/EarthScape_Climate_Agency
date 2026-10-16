import { Prose } from "./PublicLayout";

export function TermsPage() {
  return (
    <Prose>
      <h1>Terms of use</h1>
      <p>
        These terms govern use of the EarthScape Climate Agency demonstration platform built for a
        university eProject.
      </p>
      <h2>Academic use</h2>
      <p>
        The platform is provided for evaluation and teaching. Its outputs are derived from public
        climate datasets and statistical processing and are not intended for operational decision
        making.
      </p>
      <h2>Data accuracy</h2>
      <p>
        Figures reflect the observations currently stored by the system. Correlation results are
        statistical associations and are explicitly not evidence of causation. Where data is missing,
        the interface states so rather than estimating.
      </p>
      <h2>Accounts & access</h2>
      <p>
        Access is role-based and enforced by the server. Attempting to use capabilities your role does
        not include is not permitted. Keep your credentials confidential.
      </p>
    </Prose>
  );
}
