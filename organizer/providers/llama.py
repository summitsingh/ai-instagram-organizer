"""Llama provider: single/batch image analysis and content generation."""

import os
import json
import time
import requests
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor

from ..common import logger, tqdm, TQDM_AVAILABLE, _analysis_cache, _cache_lock
from ..config import Config
from ..ratelimit import LlamaRateLimiter
from ..images import encode_image_to_base64, get_exif_datetime, get_image_hash_for_cache

# Global rate limiter instance (lazily created on first Llama call)
_llama_rate_limiter = None


def analyze_images_llama_optimized(
    image_paths: List[str], config: Config
) -> List[Dict]:
    """Optimized analysis for Llama API with adaptive batch processing and circuit breaker"""
    global _llama_rate_limiter

    if _llama_rate_limiter is None:
        _llama_rate_limiter = LlamaRateLimiter(config)

    # Start with aggressive settings for speed
    max_workers = min(config.ai_parallel_workers, 15)

    logger.info(
        f"Llama high-speed processing: {len(image_paths)} images, using {max_workers} workers"
    )

    results = []
    successful_analyses = 0
    consecutive_failures = 0

    if TQDM_AVAILABLE:
        progress_bar = tqdm(total=len(image_paths), desc="Llama Analysis", unit="img")

    # Process images in adaptive batches
    i = 0
    while i < len(image_paths):
        # Adjust batch size based on current performance
        current_batch_size = _llama_rate_limiter.get_optimal_batch_size()

        # If circuit is open or many failures, process one at a time
        if _llama_rate_limiter.circuit_state == "OPEN" or consecutive_failures > 3:
            current_batch_size = 1
            max_workers = 1
        elif consecutive_failures > 0:
            current_batch_size = max(1, current_batch_size // 2)
            max_workers = min(max_workers, 2)

        # Get next batch
        batch_end = min(i + current_batch_size * max_workers, len(image_paths))
        batch_paths = image_paths[i:batch_end]

        logger.debug(
            f"Processing batch {i//current_batch_size + 1}: {len(batch_paths)} images, {max_workers} workers"
        )

        batch_results = []
        batch_failures = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit batch with staggered timing to avoid overwhelming API
            futures = []
            for j, path in enumerate(batch_paths):
                # Add small delay between submissions to spread load
                if j > 0 and j % max_workers == 0:
                    time.sleep(0.5)

                future = executor.submit(analyze_single_image_llama, path, config)
                futures.append((future, path))

            # Collect results with timeout
            for future, path in futures:
                try:
                    result = future.result(timeout=120)  # Longer timeout for stability
                    if result:
                        batch_results.append(result)
                        successful_analyses += 1
                        consecutive_failures = 0  # Reset on success
                    else:
                        batch_failures += 1
                        consecutive_failures += 1

                    if TQDM_AVAILABLE:
                        progress_bar.update(1)
                        progress_bar.set_postfix(
                            {
                                "Success": successful_analyses,
                                "Instagram-worthy": sum(
                                    1
                                    for r in results
                                    if r["analysis"].get("instagram_worthy", False)
                                ),
                                "Quality": (
                                    f"{sum(r['analysis'].get('composite_score', 0) for r in results) / len(results):.1f}/10"
                                    if results
                                    else "0/10"
                                ),
                                "Rate": f"{_llama_rate_limiter.throttle_factor:.2f}x",
                                "Circuit": _llama_rate_limiter.circuit_state,
                                "Failures": consecutive_failures,
                            }
                        )

                except Exception as e:
                    logger.error(f"Failed to analyze {os.path.basename(path)}: {e}")
                    batch_failures += 1
                    consecutive_failures += 1
                    if TQDM_AVAILABLE:
                        progress_bar.update(1)

        results.extend(batch_results)

        # Minimal delay only for high failure rates
        if batch_failures > 0:
            failure_rate = batch_failures / len(batch_paths)
            if failure_rate > 0.7:  # More than 70% failures
                time.sleep(1)  # Brief pause only

        i = batch_end

    if TQDM_AVAILABLE:
        progress_bar.close()

    logger.info(
        f"Llama adaptive analysis complete: {successful_analyses}/{len(image_paths)} successful"
    )
    logger.info(f"Final throttle factor: {_llama_rate_limiter.throttle_factor:.2f}x")
    logger.info(f"Circuit breaker state: {_llama_rate_limiter.circuit_state}")

    return results


def analyze_single_image_llama(image_path: str, config: Config) -> Optional[Dict]:
    """Analyze single image with Llama API including rate limiting and caching"""
    # Check cache first
    if config.enable_caching:
        cache_key = get_image_hash_for_cache(image_path)
        with _cache_lock:
            if cache_key in _analysis_cache:
                cache_entry = _analysis_cache[cache_key]
                cache_age_hours = (time.time() - cache_entry["timestamp"]) / 3600
                if cache_age_hours < config.cache_duration_hours:
                    return {
                        "path": image_path,
                        "analysis": cache_entry["result"],
                        "datetime": get_exif_datetime(image_path),
                    }

    # Encode image
    base64_image = encode_image_to_base64(image_path, config)
    if not base64_image:
        return None

    # Analyze with Llama
    result = analyze_with_llama(base64_image, config)
    if not result:
        return None

    # Cache result
    if config.enable_caching:
        cache_key = get_image_hash_for_cache(image_path)
        with _cache_lock:
            _analysis_cache[cache_key] = {"result": result, "timestamp": time.time()}

    return {
        "path": image_path,
        "analysis": result,
        "datetime": get_exif_datetime(image_path),
    }


def analyze_with_llama(base64_image: str, config: Config) -> Optional[Dict]:
    """Analyze image using Llama API with advanced Instagram scoring and rate limiting"""
    global _llama_rate_limiter

    if not config.llama.get("api_key"):
        logger.error(
            "Llama API key not provided. Set LLAMA_API_KEY environment variable or update config."
        )
        return None

    # Initialize rate limiter if needed
    if _llama_rate_limiter is None:
        _llama_rate_limiter = LlamaRateLimiter(config)

    prompt_text = """Analyze this image for Instagram and return ONLY a JSON object with these exact fields:

{
  "technical_score": 7,
  "visual_appeal": 8,
  "engagement_score": 6,
  "uniqueness": 5,
  "story_potential": 7,
  "category": "portrait",
  "subcategory": "casual_portrait",
  "location": "outdoor setting with mountains",
  "mood": "peaceful",
  "strengths": ["good lighting", "nice composition"],
  "weaknesses": ["slightly blurry"],
  "best_time": "afternoon",
  "caption_style": "casual",
  "hashtag_focus": "lifestyle",
  "people_present": "1",
  "time_of_day_indicators": "natural daylight"
}

Rate technical_score, visual_appeal, engagement_score, uniqueness, and story_potential from 1-10.
Choose category from: landscape, portrait, food, architecture, lifestyle, travel, nature, street, action.
Return ONLY valid JSON, no markdown formatting."""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.llama.get('api_key') or os.environ.get('LLAMA_API_KEY')}",
    }

    payload = {
        "model": config.llama["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                    },
                ],
            }
        ],
    }

    # Use advanced rate limiting with circuit breaker
    try:
        _llama_rate_limiter.acquire()
    except Exception as e:
        logger.error(f"Rate limiter blocked request: {e}")
        return None

    try:
        # Use faster timeout for individual requests
        timeout = config.llama.get("performance", {}).get("fast_timeout", 30)

        # Add retry logic for 500 errors
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    config.llama["api_url"],
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )

                if response.status_code == 500 and attempt < max_retries - 1:
                    # Server error - wait and retry
                    wait_time = (attempt + 1) * 2  # Exponential backoff
                    logger.warning(
                        f"Llama API 500 error, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(wait_time)
                    continue

                response.raise_for_status()
                break

            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    logger.warning(
                        f"Llama API request failed, retrying in {wait_time}s: {e}"
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    raise

        response_data = response.json()
        _llama_rate_limiter.release(success=True)

        # Debug: log the actual response structure
        logger.debug(f"Llama API response keys: {list(response_data.keys())}")
        logger.debug(f"Full Llama response: {response_data}")

        # Handle Llama API response format
        content = None
        if (
            "completion_message" in response_data
            and "content" in response_data["completion_message"]
        ):
            # Llama API format: completion_message.content.text
            content_obj = response_data["completion_message"]["content"]
            if isinstance(content_obj, dict) and "text" in content_obj:
                content = content_obj["text"]
            elif isinstance(content_obj, str):
                content = content_obj
        elif "choices" in response_data and len(response_data["choices"]) > 0:
            # OpenAI format (fallback)
            content = response_data["choices"][0]["message"]["content"]
        else:
            logger.warning(
                f"Unexpected Llama response format. Keys: {list(response_data.keys())}"
            )
            return None

        if content:
            # Debug: log the raw response
            logger.debug(f"Raw Llama response: {content[:200]}...")

            # Clean up response
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            try:
                analysis = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(
                    f"Failed to parse JSON, trying to extract JSON from text: {e}"
                )
                # Try to find JSON in the response
                import re

                json_match = re.search(r"\{.*\}", content, re.DOTALL)
                if json_match:
                    try:
                        analysis = json.loads(json_match.group())
                    except json.JSONDecodeError:
                        logger.error(
                            f"Could not parse extracted JSON: {json_match.group()[:100]}..."
                        )
                        return None
                else:
                    logger.error(f"No JSON found in response: {content[:100]}...")
                    return None

            # Calculate composite Instagram score
            # Check if we have at least some required fields
            required_fields = [
                "technical_score",
                "visual_appeal",
                "engagement_score",
                "uniqueness",
            ]
            missing_fields = [
                field for field in required_fields if field not in analysis
            ]

            if missing_fields:
                logger.warning(f"Missing fields in Llama response: {missing_fields}")
                # Fill in missing fields with default values
                for field in missing_fields:
                    analysis[field] = 5.0  # Default middle score

            # Enhanced weighted composite score
            weights = {
                "technical_score": 0.15,
                "visual_appeal": 0.25,
                "engagement_score": 0.30,
                "uniqueness": 0.20,
                "story_potential": 0.10,
            }

            composite_score = (
                analysis.get("technical_score", 5.0) * weights["technical_score"]
                + analysis.get("visual_appeal", 5.0) * weights["visual_appeal"]
                + analysis.get("engagement_score", 5.0) * weights["engagement_score"]
                + analysis.get("uniqueness", 5.0) * weights["uniqueness"]
                + analysis.get("story_potential", 5.0) * weights["story_potential"]
            )
            analysis["composite_score"] = round(composite_score, 2)

            # Determine tier based on composite score
            if composite_score >= 8.5:
                tier = "premium"
            elif composite_score >= 7.5:
                tier = "excellent"
            elif composite_score >= 6.0:
                tier = "good"
            elif composite_score >= 4.0:
                tier = "average"
            else:
                tier = "poor"

            analysis["instagram_tier"] = tier

            # More selective Instagram worthy determination
            analysis["instagram_worthy"] = (
                tier in ["premium", "excellent"] or composite_score >= 7.0
            )

            return analysis
        else:
            logger.warning(
                f"No valid content in Llama response. Response keys: {list(response_data.keys())}"
            )
            return None

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Llama JSON response: {e}")
        _llama_rate_limiter.release(success=False)
        return None
    except Exception as e:
        logger.error(f"Llama analysis error: {e}")
        _llama_rate_limiter.release(success=False)
        return None


def analyze_batch_with_llama(image_paths: List[str], config: Config) -> List[Dict]:
    """Analyze multiple images efficiently with Llama API using optimized batching"""
    global _llama_rate_limiter

    if not config.llama.get("api_key"):
        logger.error("Llama API key not provided")
        return []

    # Initialize rate limiter if needed
    if _llama_rate_limiter is None:
        _llama_rate_limiter = LlamaRateLimiter(config)

    # Get optimal batch size
    optimal_batch_size = _llama_rate_limiter.get_optimal_batch_size()

    logger.info(
        f"Llama batch analysis: {len(image_paths)} images, batch size: {optimal_batch_size}"
    )

    results = []

    # Process in optimal batches
    for i in range(0, len(image_paths), optimal_batch_size):
        batch_paths = image_paths[i : i + optimal_batch_size]

        # Process batch concurrently
        with ThreadPoolExecutor(max_workers=min(len(batch_paths), 8)) as executor:
            futures = []

            for path in batch_paths:
                # Encode image
                base64_image = encode_image_to_base64(path, config)
                if base64_image:
                    future = executor.submit(analyze_with_llama, base64_image, config)
                    futures.append((future, path))

            # Collect results
            for future, path in futures:
                try:
                    result = future.result(timeout=60)
                    if result:
                        results.append(
                            {
                                "path": path,
                                "analysis": result,
                                "datetime": get_exif_datetime(path),
                            }
                        )
                except Exception as e:
                    logger.error(
                        f"Batch analysis failed for {os.path.basename(path)}: {e}"
                    )

        # Small delay between batches to be respectful
        if i + optimal_batch_size < len(image_paths):
            time.sleep(0.1)

    logger.info(
        f"Llama batch analysis complete: {len(results)}/{len(image_paths)} successful"
    )
    return results


def generate_content_with_llama(
    base64_images: List[str], config: Config
) -> Optional[Dict]:
    """Generate content using Llama API with optimized processing"""
    global _llama_rate_limiter

    if not config.llama.get("api_key"):
        logger.error(
            "Llama API key not provided. Set LLAMA_API_KEY environment variable or update config."
        )
        return None

    # Initialize rate limiter if needed
    if _llama_rate_limiter is None:
        _llama_rate_limiter = LlamaRateLimiter(config)

    # Llama API has a 9 attachment limit - truncate if necessary
    if len(base64_images) > 9:
        logger.warning(
            f"Llama API supports max 9 images, truncating from {len(base64_images)} to 9"
        )
        base64_images = base64_images[:9]

    prompt_text = """You are a creative social media manager. Looking at these images, generate a JSON object with these exact keys:
    1. "caption_options": A list of 3 different, engaging Instagram caption ideas
    2. "hashtags": A list of 15-20 relevant hashtags without the # symbol
    3. "post_theme": A brief description of the overall theme
    
    Return ONLY the JSON object, no other text."""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.llama.get('api_key') or os.environ.get('LLAMA_API_KEY')}",
    }

    # Build content array with text and images
    content = [{"type": "text", "text": prompt_text}]

    for base64_image in base64_images:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
            }
        )

    payload = {
        "model": config.llama["model"],
        "messages": [{"role": "user", "content": content}],
    }

    # Use rate limiting for content generation too
    try:
        _llama_rate_limiter.acquire()
    except Exception as e:
        logger.error(f"Rate limiter blocked content generation request: {e}")
        return None

    try:
        # Use optimized timeout for content generation
        timeout = config.llama.get("performance", {}).get("fast_timeout", 45)

        # Add retry logic for 500 errors
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    config.llama["api_url"],
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )

                if response.status_code == 500 and attempt < max_retries - 1:
                    # Server error - wait and retry
                    wait_time = (attempt + 1) * 2  # Exponential backoff
                    logger.warning(
                        f"Llama content API 500 error, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(wait_time)
                    continue

                response.raise_for_status()
                break

            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    logger.warning(
                        f"Llama content API request failed, retrying in {wait_time}s: {e}"
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    raise

        response_data = response.json()
        _llama_rate_limiter.release(success=True)

        # Debug: log the actual response structure
        logger.debug(f"Llama content API response keys: {list(response_data.keys())}")

        # Handle Llama API response format
        content_text = None
        if (
            "completion_message" in response_data
            and "content" in response_data["completion_message"]
        ):
            # Llama API format: completion_message.content.text
            content_obj = response_data["completion_message"]["content"]
            if isinstance(content_obj, dict) and "text" in content_obj:
                content_text = content_obj["text"]
            elif isinstance(content_obj, str):
                content_text = content_obj
        elif "choices" in response_data and len(response_data["choices"]) > 0:
            # OpenAI format (fallback)
            content_text = response_data["choices"][0]["message"]["content"]
        else:
            logger.warning(
                f"Unexpected Llama content response format. Keys: {list(response_data.keys())}"
            )
            return None

        if content_text:

            # Clean up response
            content_text = content_text.strip()
            if content_text.startswith("```json"):
                content_text = content_text[7:]
            if content_text.endswith("```"):
                content_text = content_text[:-3]
            content_text = content_text.strip()

            parsed_content = json.loads(content_text)

            if "caption_options" in parsed_content and "hashtags" in parsed_content:
                return parsed_content
            else:
                return None
        else:
            return None

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Llama content JSON: {e}")
        _llama_rate_limiter.release(success=False)
        return None
    except Exception as e:
        logger.error(f"Llama content generation error: {e}")
        _llama_rate_limiter.release(success=False)
        return None
