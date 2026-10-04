"""Ollama provider: local image analysis and content generation."""

import json
import requests
from typing import List, Dict, Optional

from ..common import logger
from ..config import Config


def analyze_with_ollama(base64_image: str, config: Config) -> Optional[Dict]:
    """Analyze image using Ollama"""
    prompt_text = """Analyze this image for Instagram and return ONLY a JSON object with these exact fields:

{
  "technical_score": 7,
  "visual_appeal": 8,
  "engagement_score": 6,
  "uniqueness": 5,
  "story_potential": 7,
  "category": "portrait",
  "location": "outdoor setting with mountains",
  "mood": "peaceful",
  "strengths": ["good lighting", "nice composition"],
  "people_present": "1"
}

Rate technical_score, visual_appeal, engagement_score, uniqueness, and story_potential from 1-10.
Choose category from: landscape, portrait, food, architecture, lifestyle, travel, nature, street, action.
Return ONLY valid JSON, no markdown formatting."""

    payload = {
        "model": config.ollama["model"],
        "prompt": prompt_text,
        "images": [base64_image],
        "stream": False,
        "format": "json",
        "options": {"num_ctx": 4096},
    }

    try:
        response = requests.post(
            config.ollama["api_url"], json=payload, timeout=config.ollama["timeout"]
        )
        response.raise_for_status()

        response_data = response.json()
        analysis = json.loads(response_data.get("response", "{}"))

        if "instagram_worthy" in analysis:
            return analysis
        else:
            logger.warning("Invalid Ollama analysis response")
            return None

    except Exception as e:
        logger.error(f"Ollama analysis error: {e}")
        return None


def generate_content_with_ollama(
    base64_images: List[str], config: Config
) -> Optional[Dict]:
    """Generate content using Ollama"""
    prompt_text = """You are a creative social media manager. Generate a JSON object with these keys:
    1. "caption_options": A list of 3 different, engaging Instagram caption ideas
    2. "hashtags": A list of 15-20 relevant hashtags without the # symbol
    3. "post_theme": A brief description of the overall theme
    Return only the raw JSON object."""

    payload = {
        "model": config.ollama["model"],
        "prompt": prompt_text,
        "images": base64_images,
        "stream": False,
        "format": "json",
        "options": {"num_ctx": 4096},
    }

    try:
        response = requests.post(config.ollama["api_url"], json=payload, timeout=300)
        response.raise_for_status()

        response_data = response.json()
        content = json.loads(response_data.get("response", "{}"))

        if "caption_options" in content and "hashtags" in content:
            return content
        else:
            return None

    except Exception as e:
        logger.error(f"Ollama content generation error: {e}")
        return None
