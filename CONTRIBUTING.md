# Contributing to HPXML Schema API

Thank you for your interest in contributing to the HPXML Schema API!

## Development Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/canmet-energy/hpxml-schema-api.git
   cd hpxml-schema-api
   ```

2. **Install in development mode**:
   ```bash
   pip install -e ".[dev]"
   ```

3. **Run tests**:
   ```bash
   pytest tests/ -v
   ```

## Code Guidelines

- **Follow PEP 8**: Use `black` for formatting and `ruff` for linting
- **Type hints**: Add type hints for new functions and classes
- **Documentation**: Include docstrings for public functions and classes
- **Tests**: Add tests for new functionality

### Docstring Style (Google Style Standard)

All new or modified public modules, classes, functions, and methods MUST include
Google-style docstrings. Core principles:

1. Start with a concise one-line summary (imperative mood) followed by a blank line.
2. Provide context & rationale if non-obvious (design choices, trade-offs, caveats).
3. List sections in this order when applicable: Args, Returns, Raises, Yields,
   Attributes, Examples, Notes.
4. Use type hints in signatures rather than repeating types in Args unless clarification is needed.
5. Provide at least one Example for functions with non-trivial behavior or side effects.

Example (function):

```python
def merge_nodes(primary: RuleNode, secondary: RuleNode) -> RuleNode:
   """Merge two RuleNode subtrees into a combined structure.

   Args:
      primary: Node whose values take precedence when conflicts occur.
      secondary: Node providing fallback values and additional children.

   Returns:
      RuleNode: New merged node (inputs are not mutated).

   Example:
      >>> a = RuleNode(xpath="/A", name="A", kind="section")
      >>> b = RuleNode(xpath="/A", name="A", kind="section", notes=["alt"])
      >>> merged = merge_nodes(a, b)
      >>> merged.notes
      ['alt']
   """
```

Example (class with rich docstring):

```python
class EnhancedValidationEngine:
   """Facade for multi-layer HPXML validation.

   Combines schema, Schematron, custom rule, and cross-field validation while
   emitting performance metrics. Prefer this over calling BusinessRuleValidator
   directly in API layers.

   Example:
      >>> engine = EnhancedValidationEngine()
      >>> res = engine.validate_field('/HPXML/Some/Field', 'value')
      >>> res.valid
      True
   """
```

Avoid:
* Re-stating logic already obvious from the code.
* Embedding large multi-step tutorials in function docstrings (place those in `docs/`).
* Using reStructuredText field lists (``:param x:``) – the project standard is Google style.

### When to Add Examples

Provide Examples if any of these apply:
* The function has optional behaviors controlled by parameters.
* The return value shape is non-trivial (nested dicts, dataclasses, generators).
* Subtle edge cases exist (empty inputs vs populated, None handling, partial failure scenarios).

### Validation of Documentation

Pull requests may be rejected if:
* New public code lacks docstrings
* Existing docstrings are removed or degraded without justification
* Examples are missing where complexity warrants them

### Updating Existing Docstrings

If refactoring changes semantics, always update the docstring in the same commit. If behavior
changes but the docstring would mislead readers, treat it as a correctness bug.

### INTERNAL vs Public

Use a leading underscore for internal helpers (e.g., `_build_index`) and keep docstrings concise.
Only document internal helpers extensively if they encode complex or non-obvious algorithms.

### Linting / Consistency

We may introduce automated docstring linting (pydocstyle or ruff extensions). Writing consistent
Google style docstrings now minimizes future cleanup.

---

By following these documentation conventions you help ensure the API is approachable and maintainable.


## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test categories
pytest tests/test_xsd_parser.py -v
pytest tests/test_app.py -v

# Run with coverage
pytest tests/ --cov=hpxml_schema_api
```

## Code Style

```bash
# Format code
black src/ tests/

# Check linting
ruff check src/ tests/

# Type checking
mypy src/
```

## Submitting Changes

1. **Fork the repository** on GitHub
2. **Create a feature branch** from `main`
3. **Make your changes** with tests and documentation
4. **Run the full test suite** to ensure nothing is broken
5. **Submit a pull request** with a clear description

## Pull Request Guidelines

- **Clear description**: Explain what the PR does and why
- **Tests**: Include tests for new functionality
- **Documentation**: Update README or docstrings as needed
- **Clean commits**: Use clear commit messages
- **Single purpose**: One feature or fix per PR

## Reporting Issues

When reporting bugs or requesting features:

- **Search existing issues** first to avoid duplicates
- **Provide clear description** of the problem or feature request
- **Include code examples** when reporting bugs
- **Specify environment details** (Python version, OS, etc.)

## Questions?

Feel free to open an issue for questions about:
- HPXML schema interpretation
- API design decisions
- Development setup problems
- Feature implementation guidance

We appreciate your contributions to making HPXML more accessible!