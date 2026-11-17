"use client";

// A <select> that submits its form as soon as you pick a value.
// When the state changes, the district choice is cleared so you never
// end up with "Maharashtra + Bhopal" (0 results).
export default function AutoSubmit(props: React.SelectHTMLAttributes<HTMLSelectElement> & { clears?: string }) {
  const { clears, ...rest } = props;
  return (
    <select
      {...rest}
      onChange={(e) => {
        const form = e.currentTarget.form;
        if (!form) return;
        if (clears) {
          const other = form.elements.namedItem(clears) as HTMLSelectElement | null;
          if (other) other.value = "";
        }
        const page = form.elements.namedItem("page") as HTMLInputElement | null;
        if (page) page.value = "1";
        form.requestSubmit();
      }}
    />
  );
}
