"""
Diagram API endpoints for generating and serving diagram images.

This module provides REST API endpoints for generating diagrams
from Mermaid syntax and serving them as images.
"""

import base64
import io
import uuid
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
import requests
from loguru import logger

router = APIRouter(prefix="/api/diagrams", tags=["Diagrams"])


class DiagramRequest(BaseModel):
    mermaid_code: str
    format: str = "svg"  # svg, png, or pdf
    theme: str = "default"


@router.post("/generate")
async def generate_diagram(request: DiagramRequest):
    """Generate a diagram from Mermaid syntax and return as image."""
    try:
        # Use Mermaid Live Editor API to generate the diagram
        # This is a free service that can generate diagrams from Mermaid syntax
        api_url = "https://mermaid.ink/img/"
        
        # Encode the Mermaid code as base64
        mermaid_b64 = base64.b64encode(request.mermaid_code.encode()).decode()
        
        # Construct the URL
        diagram_url = f"{api_url}{mermaid_b64}?type={request.format}&theme={request.theme}"
        
        # Generate a unique ID for this diagram
        diagram_id = str(uuid.uuid4())
        
        # Store the diagram info (in a real app, you'd store this in a database)
        # For now, we'll just return the URL
        return {
            "diagram_id": diagram_id,
            "url": diagram_url,
            "format": request.format,
            "theme": request.theme
        }
        
    except Exception as e:
        logger.error(f"Error generating diagram: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate diagram: {str(e)}")


@router.get("/{diagram_id}")
async def get_diagram(
    diagram_id: str,
    format: str = Query("svg", description="Image format: svg, png, or pdf"),
    theme: str = Query("default", description="Mermaid theme")
):
    """Get a diagram by ID (placeholder for future implementation)."""
    # This would typically retrieve a stored diagram
    # For now, return a placeholder
    raise HTTPException(status_code=404, detail="Diagram not found")


@router.get("/preview/{mermaid_b64}")
async def preview_diagram(
    mermaid_b64: str,
    format: str = Query("svg", description="Image format: svg, png, or pdf"),
    theme: str = Query("default", description="Mermaid theme")
):
    """Preview a diagram from base64-encoded Mermaid syntax."""
    try:
        # Decode the base64 Mermaid code
        mermaid_code = base64.b64decode(mermaid_b64).decode()
        
        # Use Mermaid Live Editor API
        api_url = "https://mermaid.ink/img/"
        diagram_url = f"{api_url}{mermaid_b64}?type={format}&theme={theme}"
        
        # Fetch the image
        response = requests.get(diagram_url, timeout=30)
        response.raise_for_status()
        
        # Return the image
        content_type = f"image/{format}" if format != "svg" else "image/svg+xml"
        return Response(
            content=response.content,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=3600",  # Cache for 1 hour
                "Access-Control-Allow-Origin": "*"
            }
        )
        
    except Exception as e:
        logger.error(f"Error previewing diagram: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to preview diagram: {str(e)}")
