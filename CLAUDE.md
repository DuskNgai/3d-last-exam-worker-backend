## Code Review & Development Guidelines

1. Development Principles
  - Documentation first: Write documentation before implementing code.
  - Coding standards: Emphasize readability, reusability, maintainability, and encapsulation, but avoid over‑encapsulation and over‑abstraction.
  - Organization: Keep the code well‑organized and clearly modular.
  - Commenting: Only explain the reasoning behind non‑obvious code; do not comment on the obvious.
  - Code cleanup: Remove all deprecated code; do not keep old implementations. Do not maintain backward compatibility unless explicitly required.
2. Tool Usage
  - For well‑defined and isolated tasks, prefer using subagents to improve efficiency and isolation of context.
3. Review Process
  - When the user requests a review, discuss the results item by item. For each item, clearly state the issue and provide a concrete suggestion, then apply the revision immediately after the user confirms.
  - If a revision affects subsequent items, dynamically update their conclusions before proceeding, ensuring overall consistency.
