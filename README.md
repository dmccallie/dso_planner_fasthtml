# DSO Planner FastHTML

A Deep Sky Object (DSO) planner and interactive sky map viewer built with FastHTML and D3.js.
THIS IS INCOMPLETE TESTING CODE - DOES NOT REALLY WORK YET!

## Features

- **Interactive Sky Map**: Smooth mouse-controlled navigation with SkySafari-like drag functionality
- **DSO Database**: Browse and filter deep sky objects with detailed information
- **Moon Phase Planning**: Visual moon phase charts to plan optimal viewing sessions
- **Location-aware**: Astronomy calculations based on observer location
- **Real-time Updates**: HTMX-powered interface for instant form updates and filtering

## Technology Stack

- **Backend**: FastHTML (Python web framework)
- **Frontend**: HTMX, D3.js for interactive visualizations
- **Astronomy**: AstroPlan, Astronomy Engine for calculations
- **Database**: SQLite for DSO data storage

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/dso_planner_fasthtml.git
cd dso_planner_fasthtml
```

2. Install dependencies with uv:
```bash
uv sync
```

3. Run the application:
```bash
uv run python main.py
```

4. Open your browser to `http://localhost:5001`

## Usage

- **Sky Map**: Use mouse to drag and navigate the sky map
- **DSO Filters**: Use the filter form to narrow down objects by type, magnitude, etc.
- **Moon Planning**: Check moon phase charts to plan dark sky sessions

## Development

The project uses:
- `pyproject.toml` for dependency management
- `uv` as the package manager
- FastHTML for rapid web development
- D3.js for astronomical visualizations

## License

MIT License
