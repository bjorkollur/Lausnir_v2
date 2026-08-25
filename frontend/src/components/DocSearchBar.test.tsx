import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DocSearchBar } from "./DocSearchBar";

const baseProps = {
  onQueryChange: () => {},
  useRegex: false,
  onUseRegexChange: () => {},
  regexValid: true,
  onNext: () => {},
  onPrev: () => {},
};

describe("DocSearchBar", () => {
  it("shows the match counter when there are results", () => {
    render(<DocSearchBar {...baseProps} query="dómur" matchCount={5} activeIndex={2} />);
    expect(screen.getByText("3 af 5")).toBeInTheDocument();
  });

  it("shows 'Engar niðurstöður' when the query has no matches", () => {
    render(<DocSearchBar {...baseProps} query="eitthvað" matchCount={0} activeIndex={0} />);
    expect(screen.getByText("Engar niðurstöður")).toBeInTheDocument();
  });

  it("shows nothing when the query is under 2 characters", () => {
    render(<DocSearchBar {...baseProps} query="d" matchCount={0} activeIndex={0} />);
    expect(screen.queryByText("Engar niðurstöður")).not.toBeInTheDocument();
  });

  it("disables prev/next when there are no matches", () => {
    render(<DocSearchBar {...baseProps} query="eitthvað" matchCount={0} activeIndex={0} />);
    expect(screen.getByLabelText("Næsta niðurstaða")).toBeDisabled();
    expect(screen.getByLabelText("Fyrri niðurstaða")).toBeDisabled();
  });

  it("Enter calls onNext, Shift+Enter calls onPrev", async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    const onPrev = vi.fn();
    render(
      <DocSearchBar {...baseProps} query="dómur" matchCount={3} activeIndex={0} onNext={onNext} onPrev={onPrev} />,
    );
    const input = screen.getByLabelText("Leita í skjalinu");
    await user.type(input, "{Enter}");
    expect(onNext).toHaveBeenCalledOnce();
    await user.type(input, "{Shift>}{Enter}{/Shift}");
    expect(onPrev).toHaveBeenCalledOnce();
  });

  it("clicking the .* button toggles regex mode", async () => {
    const user = userEvent.setup();
    const onUseRegexChange = vi.fn();
    render(
      <DocSearchBar {...baseProps} query="" matchCount={0} activeIndex={0} onUseRegexChange={onUseRegexChange} />,
    );
    await user.click(screen.getByLabelText("Regex leit"));
    expect(onUseRegexChange).toHaveBeenCalledWith(true);
  });

  it("shows the regex toggle as pressed when useRegex is on", () => {
    render(<DocSearchBar {...baseProps} query="" useRegex={true} matchCount={0} activeIndex={0} />);
    expect(screen.getByLabelText("Regex leit")).toHaveAttribute("aria-pressed", "true");
  });

  it("shows an invalid-pattern message instead of the match counter", () => {
    render(
      <DocSearchBar {...baseProps} query="[" useRegex={true} regexValid={false} matchCount={0} activeIndex={0} />,
    );
    expect(screen.getByText("Ógilt regex mynstur")).toBeInTheDocument();
    expect(screen.queryByText("Engar niðurstöður")).not.toBeInTheDocument();
  });

  it("shows a match count for a single-character valid regex pattern", () => {
    render(<DocSearchBar {...baseProps} query="a" useRegex={true} matchCount={7} activeIndex={0} />);
    expect(screen.getByText("1 af 7")).toBeInTheDocument();
  });
});
