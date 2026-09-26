---
name: check-code-conventions
description: Use when writing or reviewing Python code to verify correctness and consistency with the project's conventions.
---

# Check Code Conventions

## Overview

Verify Python code follows project conventions for naming, types, error handling, string formatting, and common pitfalls.

## Checklist

Run through applicable items for each Python file:

### 1. Non-ASCII Characters

- [ ] No non-ASCII characters in code body, comments, or docstrings: identifiers, comments, and docstrings must use only ASCII characters (U+0000 to U+007F)
- [ ] No non-ASCII in code-authored messages: exception messages, log messages, and format string patterns must use ASCII (the pattern itself, regardless of interpolated values)
- [ ] Data strings may contain non-ASCII: variable values, test fixtures, and enum values matching external systems are exempt

### 2. Modules & Packages

- [ ] File name is `snake_case`: `calculator.py`, `helpers.py`
- [ ] Test files use `test_*.py` naming
- [ ] Test classes use `Test<ClassName>` naming: `TestShoppingCart`, `TestDataStore`
- [ ] Test functions use `test_<what>_<scenario>` naming: `test_add_item_empty_cart`, `test_remove_item_not_found`
- [ ] Package name is `snake_case`: `core/`, `utils/`
- [ ] Every package contains `__init__.py`; re-exports only, no function or class definitions

### 3. Imports

- [ ] No `from module import *`: use explicit imports
- [ ] No `Dict` imported from `typing`: use `dict[str, int]`
- [ ] No `List` imported from `typing`: use `list[str]`
- [ ] No `Optional` imported from `typing`: use `str | None`
- [ ] No `Tuple` imported from `typing`: use `tuple[int, str]`
- [ ] No `Union` imported from `typing`: use `int | str`
- [ ] Each `from typing` import is from the allowed set: `Any`, `Callable`, `Final`, `Literal`, `Never`, `overload`, `Protocol`, `Self`, `TYPE_CHECKING`, `TypeAlias`, `TypeGuard`, `TypedDict`, `TypeVar`
- [ ] Abstract base classes for type annotations are imported from `collections.abc`, not `typing`: `Iterable`, `Sequence`, `Mapping`, `MutableSequence`, etc.

### 4. Classes

- [ ] Class name is `PascalCase`: `ShoppingCart`, `DataStore`
- [ ] Exception class names use `Error` suffix: `class ParseError(Exception)`
- [ ] `TypeVar` naming: constrained uses descriptive names (`AnyStr`), variant types use suffix (`_T`, `_T_co`)
- [ ] Protocol class names use `Supports*`/`Has*` prefix or `*able` suffix: `SupportsRead`, `HasLength`, `Iterable`
- [ ] Type aliases are `PascalCase` defined at module level, with docstring on the next line: `AxesType = tuple[int, ...]` followed by `"""..."""` on the next line

### 5. Functions & Methods

- [ ] Function/method name is `snake_case`: `calculate_total()`, `fetch_data()`
- [ ] Instance method first parameter is `self`, class method first parameter is `cls`
- [ ] Private functions/attributes use `_` prefix (including `@property` backing fields, unless the property is computed with no backing field): `_parse_args()`, `self._name`
- [ ] Function name prefixes follow role: `create_` for factories, `to_`/`from_`/`as_` for conversions, `is_`/`has_`/`can_` for booleans, `on_` for event handlers
- [ ] No mutable default arguments: use `None` as sentinel and assign the actual default inside the body, never `def f(items=[])`
- [ ] Never bare `except:`; use `except Exception:` at minimum to avoid catching `KeyboardInterrupt` and `SystemExit`
- [ ] When re-raising a caught exception as a different type, chain with `from`: `raise NewError(...) from original` preserves the original traceback; use `from None` to intentionally suppress it
- [ ] External resources (files, sockets, locks, db connections) must use `with` statements; never manual `.close()`
- [ ] Use f-strings for string formatting; `%` formatting is forbidden; `.format()` permitted only for deferred templates. Exception: `logging` module may use `%` for deferred interpolation (e.g., `logger.debug("user %s", user_id)`); never pass an f-string to a logging call
- [ ] Closures inside loops must bind loop variables explicitly: `lambda i=i: ...` or `functools.partial(f, i)`, never bare `lambda: i`
- [ ] Single-line docstring may use `"""Short summary."""` on one line; multi-line docstring must have `"""` alone on first line, text starting next line. Applies to module, class, and function docstrings
- [ ] Docstring and code line width limit is 140 characters. When wrapping docstrings, break at punctuation marks (commas, periods, semicolons, colons) not mid-word
- [ ] Public and non-trivial functions use `` `name` (Type): desc `` format for `Args:`, `Returns:`, and `Raises:` (when the function raises exceptions) sections; trivial or private functions may use a single-line summary
- [ ] When prose (comments, docstrings, logs, exception messages) requires a parenthetical break: rewrite the sentence using commas, semicolons, periods, or colons; or restructure the sentence.

### 6. Variables

- [ ] Local variables are `snake_case`: `total_count`, `temp_list`
- [ ] Identifier names avoid abbreviations: `relative_path` not `rel`, `future` not `fut`; widely known abbreviations are exempt: `url`, `args`, `tmp`
- [ ] `_` is used for throwaway variables: `for _ in range(10)`, `a, _, c = triple`
- [ ] Module-level constants use `UPPER_SNAKE_CASE` and are annotated with `Final`: `MAX_SIZE: Final = 100`
- [ ] Config values should be loaded from `.env` and bound to module-level constants, rather than read from environment directly inside functions
- [ ] CLI argument defaults (e.g. `DEFAULT_DATA_ROOT`) are defined in the CLI entry module, not in `core/`; `core/` receives them as explicit parameters
- [ ] Compare to `None` with `is`/`is not`, never `==`/`!=`

### 7. Enums

- [ ] Enum classes inherit `StrEnum` or `IntEnum`, not bare `Enum`
- [ ] Enum member names are `UPPER_SNAKE_CASE`
- [ ] Enum member values use strings matching external systems (e.g., `"running"`, `"stopped"`)

### 8. Types & Annotations

- [ ] Data-only classes (no methods beyond `__init__` and `__post_init__`) use `@dataclass`
- [ ] No mutable default values (`= []`, `= {}`, `= set()`, or any mutable object): use `field(default_factory=...)`
- [ ] No business logic methods: logic belongs in dedicated functions or separate classes, not in the dataclass
- [ ] No bare generics: use `list[str]` not `list`, `dict[str, int]` not `dict`, `tuple[int, str]` not `tuple`
- [ ] Generic class uses `class My[T]:` syntax, not `class My(Generic[T]):`
- [ ] `Callable` must specify parameter types and a concrete (non-`Any`) return type: `Callable[[int, str], bool]`; `Callable[..., bool]` is acceptable when parameter types are unconstrained. Never bare `Callable` or `Callable[..., Any]`
- [ ] `__init__` does not have a return type annotation
- [ ] Use `Self` for methods returning the class instance: `def clone(self) -> Self:`, not `-> ClassName`
- [ ] `Literal` uses only literal values: `Literal["a", "b"]`, not variables or computed expressions
- [ ] Use `Never` for functions that never return: `def fatal(msg: str) -> Never:`
- [ ] Use `@overload` for functions with multiple distinct signatures; implementation signature comes last without `@overload`

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Non-ASCII chars in code-authored text: `×` (multiplication sign), `—` (em-dash), `…` (ellipsis), curly/smart quotes | `×` → `*` or `\times` (LaTeX/math). `—`/`–` → rewrite sentence without dashes (comma, semicolon, period, colon). `…` → `...`. Quotes → straight `'` `"` |
| `__init__.py` containing function/class definitions | Move to appropriate module |
| Missing `__init__.py` in package | Create `__init__.py` and re-export the public API |
| `from typing import List, Dict` | Remove import, use `list`, `dict` |
| `from module import *` | Import specific names explicitly |
| `from typing import Iterable, Sequence` | `from collections.abc import Iterable, Sequence` |
| `class shoppingcart:` | `class ShoppingCart:` |
| `class ParseException(Exception):` | `class ParseError(Exception):` |
| `TypeVar("T")` with no suffix | Use descriptive name or suffix `_T`/`_T_co` |
| `def CalculateTotal():` | `def calculate_total():` |
| `def createClient():` | `def create_client():` |
| `def check():` returning bool | `def is_valid():` (use `is_`/`has_`/`can_` prefix for bool return) |
| `def handle_click():` (missing `on_` prefix) | `def on_click():` |
| `def method(x, y):` without `self` as first param | `def method(self, x, y):` |
| `@property` stores value in `self.name` instead of `self._name` | Rename: store in `self._name`, expose via `@property` `self.name` |
| `def f(items=[]):` | `def f(items: list[str] | None = None):` with `if items is None: items = []` in body |
| `"""Long text on same line as opener...` | `"""` alone on first line, text starting next line |
| Docstring missing `Raises:` for raised exceptions | `` `Raises:` ExceptionType: condition `` |
| `except:` (bare) | `except Exception:`; never catch `KeyboardInterrupt`/`SystemExit` |
| `raise NewError(...)` without `from` in `except` block | `raise NewError(...) from original`; preserves original traceback |
| `f = open(path)` without `with` | `with open(path) as f:`; never leak resources |
| `"Hello %s" % name` or `"Hello {}".format(name)` | `f"Hello {name}"` |
| `logger.debug(f"user {user_id}")` | `logger.debug("user %s", user_id)`; logging uses deferred `%` interpolation |
| `lambda: i` inside loop | `lambda i=i: i` or `functools.partial(f, i)`; bind loop vars at definition time |
| `os.environ.get("KEY")` inside function | Prefer binding to module-level constant |
| CLI default constant like `DEFAULT_DATA_ROOT` defined in `core/` | Define it in the CLI entry module; pass values into `core/` as explicit parameters |
| `itemCount = 5` (local variable) | `item_count = 5` |
| `rel`, `fut` (abbreviated identifiers) | `relative_path`, `future`; avoid abbreviations unless widely known (`url`, `args`, `tmp`) |
| `x: Final = 5` inside function | `MAX_SIZE: Final = 100` at module level |
| `if x == None:` | `if x is None:` |
| Using `data` as loop variable when it's unused | `for _ in range(10)`, `a, _, c = triple` |
| Enum member `running = "running"` | `RUNNING = "running"` |
| `class Status(Enum):` | `class Status(StrEnum):` or `class Status(IntEnum):` |
| `Optional[X]` | `X \| None` |
| `Union[A, B]` | `A \| B` |
| `Tuple[X, Y]` | `tuple[X, Y]` |
| `tags: list[str] = []` | `tags: list[str] = field(default_factory=list)` |
| `tags: dict[str, int] = {}` | `tags: dict[str, int] = field(default_factory=dict)` |
| Adding `validate()` to dataclass | Move to a dedicated function or separate class |
| `list` instead of `list[str]` | Add type parameter: `list[str]` |
| `Callable` without signature | Add parameter and return types: `Callable[[int], bool]` |
| `def __init__(self) -> None:` | Remove return type annotation |
| `def clone(self) -> MyClass:` | `def clone(self) -> Self:` |
| `Literal[some_var]` | Use literal values: `Literal["a", "b"]` |
| `class My(Generic[T]):` | `class My[T]:` |
| `def fatal(msg: str) -> NoReturn:` | `def fatal(msg: str) -> Never:` |
| Multiple signatures via `Union` in one function | Use `@overload` for each signature |
