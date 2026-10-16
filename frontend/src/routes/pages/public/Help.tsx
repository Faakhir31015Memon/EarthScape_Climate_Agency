import { Link } from "react-router-dom";
import { Prose } from "./PublicLayout";

export function HelpPage() {
  return (
    <Prose>
      <h1>Help</h1>
      <h2>Getting started</h2>
      <p>
        Sign in with the account issued to you. If self-service registration is enabled on this
        deployment you can create an account from the register page; otherwise an administrator
        provisions accounts.
      </p>
      <h2>Why am I asked to change my password?</h2>
      <p>
        Accounts created with an initial password are flagged to rotate it on first sign-in. The
        workspace will prompt you until the password is changed.
      </p>
      <h2>I see “your role cannot view this”</h2>
      <p>
        Access is enforced by capability (for example <code>user.manage</code> or{" "}
        <code>dataset.upload</code>). Your role may not include it. Request access from an
        administrator.
      </p>
      <h2>Empty charts and maps</h2>
      <p>
        When a chart or map shows a reason instead of data, that reason is returned by the API — it
        means no observations match the filters or nothing has been ingested for that variable yet.
        We never display placeholder values.
      </p>
      <h2>Need more help?</h2>
      <p>
        Sign in and open the support area to raise a ticket.{" "}
        <Link to="/login" className="font-medium text-primary hover:underline">
          Go to sign in
        </Link>
        .
      </p>
    </Prose>
  );
}
