"""Python 3.14 compatibility patch for Frappe 16.35.0."""
import sys


def main() -> None:
    target = "fde_bench/apps/frappe/frappe/utils/__init__.py"
    with open(target) as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        if line.strip() == "if key in v:":
            indent = line[: len(line) - len(line.lstrip())]
            lines[i] = (
                indent + 'if hasattr(v, "__contains__") and key in v:\n'
            )
            print("Patched line", i + 1)
            break
    else:
        print("ERROR: Expected line 'if key in v:' not found in " + target)
        sys.exit(1)

    with open(target, "w") as f:
        f.writelines(lines)


if __name__ == "__main__":
    main()
