# Overview

I am an amateur coder and I want to improve my coding experience. As I have a hard time getting the gist of my code projects and their growing complexity, I am building a code visualiser. I have 10+ hobby projects, many consisting of fastapi backend with js & html frontend. My largest project is a Local LLM server code that spans several thousand LOC.
The code visualiser is a fastapi backend, js / html frontend as well. 

I want to be able to select a code project of mine. It then either stores the code elements in a db or retrieves them from the db. A code element is something like a function, a definition, an import etc. The visualiser should then offer different view options. For single files, I can see a canvas with rectangles, each rectangle being a function for example (or a code element in general). I can expand elements to see their actual code. Also, in another view, I can see the code file and those parts of it that have been identified as a code element are marked. This is so I can make sure my marking logic works. Another important view is the so called "workflow" view, here I can see workflows in my projects. That means, a canvas, with rectangles connected. A workflow is a series of functions that are called, one after another. In the canvas, I can select what kind of workflows I want to see. E.g. workflows spanning at least 5 function calls, or workflows starting through the press of a button in my frontend.

The backend should work in realtime, and update the files as I edit them, to make sure it is update, while I am coding, so I can keep an overview.

# Continue from 15_09

For File Overview
- add file summaries to rectangles, once they exist

Continue with workflow overview

Parsing second project not working. Get to bottom of this issue and fix once and for all

# Structure

- Backend Code Parsing

- [x] Feature Code Viewer, see the elements identified per file with lines marked
    - [x] First of all only initial parsing, if not yet parsed
        - [ ] Add feature to automatically / constantly parse: If change in file vs saved file (or new save?), reparse file
    

- [x] Feature Code Overview, single file view of code elements
    - canvas, zoomable, panable, with rectangles, those that interact are connected
        - logic for connection ?

- [ ] Feature overall overview
    - only show workflows

