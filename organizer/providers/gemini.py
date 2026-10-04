"""Gemini provider: single/batch image analysis and content generation."""
import os
import base64
import json
import time
import random
import requests
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor

from ..common import logger, tqdm, TQDM_AVAILABLE
from ..config import Config
from ..ratelimit import GeminiRateLimiter
from ..images import encode_image_to_base64, get_exif_datetime


def analyze_images_gemini_optimized(image_paths: List[str], config: Config) -> List[Dict]:
    """Optimized Gemini analysis with conservative rate limiting and circuit breaker"""
    logger.info(f"Gemini processing: {len(image_paths)} images with conservative rate limiting")
    
    # Initialize Gemini rate limiter
    rate_limiter = GeminiRateLimiter(config)
    results = []
    
    # Use much lower concurrency for Gemini free tier
    max_workers = 3  # Conservative for Gemini's 30 req/sec limit
    
    if TQDM_AVAILABLE:
        progress_bar = tqdm(total=len(image_paths), desc="Gemini Analysis", unit="img")
    
    # Process in smaller batches with intelligent sizing
    batch_size = rate_limiter.get_optimal_batch_size()
    batches = [image_paths[i:i + batch_size] for i in range(0, len(image_paths), batch_size)]
    
    logger.info(f"Processing {len(batches)} batches of size {batch_size} with {max_workers} workers")
    
    for batch_idx, batch in enumerate(batches):
        logger.info(f"Processing batch {batch_idx + 1}/{len(batches)} ({len(batch)} images)")
        
        batch_results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            
            for path in batch:
                future = executor.submit(analyze_single_image_gemini_with_limiter, path, config, rate_limiter)
                futures.append((future, path))
            
            for future, path in futures:
                try:
                    result = future.result(timeout=120)  # Longer timeout for rate-limited requests
                    if result:
                        batch_results.append(result)
                        results.append(result)
                    
                    if TQDM_AVAILABLE:
                        progress_bar.update(1)
                        progress_bar.set_postfix({
                            'Success': len(results),
                            'Batch': f"{batch_idx + 1}/{len(batches)}",
                            'Throttle': f"{rate_limiter.throttle_factor:.2f}",
                            'Circuit': rate_limiter.circuit_state
                        })
                except Exception as e:
                    logger.error(f"Gemini analysis failed for {os.path.basename(path)}: {e}")
                    if TQDM_AVAILABLE:
                        progress_bar.update(1)
        
        # Adaptive delay between batches based on performance
        if batch_idx < len(batches) - 1:  # Don't delay after last batch
            if rate_limiter.circuit_state == "OPEN":
                delay = 10.0  # Long delay if circuit is open
            elif rate_limiter.throttle_factor < 0.5:
                delay = 5.0   # Medium delay if heavily throttled
            elif len(batch_results) < len(batch) * 0.8:  # Less than 80% success
                delay = 3.0   # Short delay if some failures
            else:
                delay = 1.0   # Minimal delay if all good
            
            logger.debug(f"Inter-batch delay: {delay}s (throttle: {rate_limiter.throttle_factor:.2f})")
            time.sleep(delay)
        
        # Adjust batch size for next iteration
        new_batch_size = rate_limiter.get_optimal_batch_size()
        if new_batch_size != batch_size:
            batch_size = new_batch_size
            logger.info(f"Adjusted batch size to {batch_size} based on performance")
    
    if TQDM_AVAILABLE:
        progress_bar.close()
    
    logger.info(f"Gemini processing complete: {len(results)}/{len(image_paths)} images analyzed successfully")
    return results


def analyze_single_image_gemini_with_limiter(image_path: str, config: Config, rate_limiter: GeminiRateLimiter) -> Optional[Dict]:
    """Analyze single image with Gemini API using rate limiter"""
    try:
        # Acquire rate limit permission
        rate_limiter.acquire()
        
        # Perform the actual analysis
        result = analyze_single_image_gemini_direct(image_path, config)
        
        # Record success/failure
        rate_limiter.release(success=result is not None)
        
        return result
        
    except Exception as e:
        # Record failure
        rate_limiter.release(success=False)
        logger.error(f"Rate-limited Gemini analysis failed for {os.path.basename(image_path)}: {e}")
        return None


def analyze_single_image_gemini_direct(image_path: str, config: Config) -> Optional[Dict]:
    """Analyze single image with Gemini API (direct call without rate limiting)"""
    try:
        # Convert image to base64
        with open(image_path, "rb") as img:
            base64_image = base64.b64encode(img.read()).decode('utf-8')
        
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
        
        url = f"{config.gemini['api_url']}/{config.gemini['model']}:generateContent"
        
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt_text},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": base64_image
                        }
                    }
                ]
            }],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 1024,
            }
        }
        
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": config.gemini["api_key"]
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        
        result = response.json()
        
        if 'candidates' in result and len(result['candidates']) > 0:
            content = result['candidates'][0]['content']['parts'][0]['text']
            
            # Clean up response
            content = content.strip()
            if content.startswith('```json'):
                content = content[7:]
            if content.endswith('```'):
                content = content[:-3]
            content = content.strip()
            
            analysis = json.loads(content)
            
            # Calculate composite score
            weights = {
                'technical_score': 0.15,
                'visual_appeal': 0.25,
                'engagement_score': 0.30,
                'uniqueness': 0.20,
                'story_potential': 0.10
            }
            
            composite_score = sum(
                analysis.get(field, 5.0) * weight 
                for field, weight in weights.items()
            )
            
            analysis['composite_score'] = round(composite_score, 2)
            
            # Determine tier
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
            
            analysis['instagram_tier'] = tier
            analysis['instagram_worthy'] = tier in ['premium', 'excellent'] or composite_score >= 7.0
            
            return {
                'image_path': image_path,
                'analysis': analysis,
                'provider': 'gemini'
            }
    
    except Exception as e:
        logger.error(f"Gemini analysis failed for {os.path.basename(image_path)}: {e}")
        return None


def make_rate_limited_request(url: str, payload: dict, headers: dict, config: Config, request_type: str = "batch") -> dict:
    """Make a rate-limited request with exponential backoff"""
    max_retries = config.ai_max_retries
    base_delay = 3.0 if request_type == "batch" else 1.0  # Longer delay for batch requests
    
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=config.ai_timeout)
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:  # Rate limit error
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 2)
                    logger.warning(f"{request_type.title()} rate limit hit, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries + 1})")
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"{request_type.title()} rate limit exceeded after {max_retries + 1} attempts")
                    raise
            elif e.response.status_code == 503:  # Service unavailable
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt) + random.uniform(2, 5)  # Longer delay for 503
                    logger.warning(f"{request_type.title()} service unavailable (503), retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries + 1})")
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"{request_type.title()} service unavailable after {max_retries + 1} attempts")
                    raise
            else:
                logger.error(f"{request_type.title()} Gemini analysis error: {e}")
                raise
        except Exception as e:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"{request_type.title()} request failed, retrying in {delay:.1f}s: {e}")
                time.sleep(delay)
                continue
            else:
                logger.error(f"{request_type.title()} request failed after {max_retries + 1} attempts: {e}")
                raise


def analyze_batch_with_gemini(image_paths: List[str], config: Config) -> List[Dict]:
    """Analyze multiple images in a single Gemini API request (up to 16 images)"""
    if len(image_paths) > 16:
        raise ValueError("Gemini batch analysis supports maximum 16 images per request")
    
    logger.info(f"Batch analyzing {len(image_paths)} images with Gemini")
    
    # Encode all images
    base64_images = []
    valid_paths = []
    
    for path in image_paths:
        encoded = encode_image_to_base64(path, config)
        if encoded:
            base64_images.append(encoded)
            valid_paths.append(path)
    
    if not base64_images:
        return []
    
    # Create batch prompt
    prompt_text = f"""Analyze these {len(base64_images)} images for Instagram and return a JSON array with {len(base64_images)} objects, each with these exact fields:

{{
  "technical_score": 7,
  "visual_appeal": 8,
  "engagement_score": 6,
  "uniqueness": 5,
  "story_potential": 7,
  "category": "portrait",
  "location": "outdoor setting with mountains",
  "mood": "peaceful"
}}

Rate technical_score, visual_appeal, engagement_score, uniqueness, and story_potential from 1-10.
Choose category from: landscape, portrait, food, architecture, lifestyle, travel, nature, street, action.
Return ONLY a JSON array with {len(base64_images)} objects, no markdown formatting."""

    url = f"{config.gemini['api_url']}/{config.gemini['model']}:generateContent"
    
    # Create parts array with text prompt and all images
    parts = [{"text": prompt_text}]
    for base64_image in base64_images:
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64_image
            }
        })
    
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 4096,
        }
    }
    
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": config.gemini["api_key"]
    }
    
    try:
        response_data = make_rate_limited_request(url, payload, headers, config, "batch")
        
        if 'candidates' in response_data and len(response_data['candidates']) > 0:
            content = response_data['candidates'][0]['content']['parts'][0]['text']
            
            # Clean up response
            content = content.strip()
            if content.startswith('```json'):
                content = content[7:]
            if content.endswith('```'):
                content = content[:-3]
            content = content.strip()
            
            try:
                analyses = json.loads(content)
                if not isinstance(analyses, list):
                    analyses = [analyses]  # Handle single object response
                
                results = []
                for i, analysis in enumerate(analyses[:len(valid_paths)]):
                    # Fill in missing fields with defaults
                    required_fields = ['technical_score', 'visual_appeal', 'engagement_score', 'uniqueness']
                    for field in required_fields:
                        if field not in analysis:
                            analysis[field] = 5.0
                    
                    # Calculate composite score
                    weights = {
                        'technical_score': 0.15,
                        'visual_appeal': 0.25,
                        'engagement_score': 0.30,
                        'uniqueness': 0.20,
                        'story_potential': 0.10
                    }
                    
                    composite_score = (
                        analysis.get('technical_score', 5.0) * weights['technical_score'] +
                        analysis.get('visual_appeal', 5.0) * weights['visual_appeal'] +
                        analysis.get('engagement_score', 5.0) * weights['engagement_score'] +
                        analysis.get('uniqueness', 5.0) * weights['uniqueness'] +
                        analysis.get('story_potential', 5.0) * weights['story_potential']
                    )
                    analysis['composite_score'] = round(composite_score, 2)
                    
                    # Determine tier
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
                    
                    analysis['instagram_tier'] = tier
                    analysis['instagram_worthy'] = tier in ['premium', 'excellent'] or composite_score >= 7.0
                    
                    results.append({
                        'path': valid_paths[i],
                        'analysis': analysis,
                        'datetime': get_exif_datetime(valid_paths[i])
                    })
                
                return results
                
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse batch Gemini JSON response: {e}")
                return []
        else:
            logger.warning("No candidates in batch Gemini response")
            return []
            
    except Exception as e:
        logger.error(f"Batch Gemini analysis error: {e}")
        
        # If it's a 503 error, fall back to individual analysis
        if "503" in str(e) or "Service Unavailable" in str(e):
            logger.warning("Gemini batch service unavailable, falling back to individual analysis")
            return analyze_images_individually_fallback(valid_paths, config)
        
        return []


def analyze_images_individually_fallback(image_paths: List[str], config: Config) -> List[Dict]:
    """Fallback to individual analysis when batch fails"""
    logger.info(f"Analyzing {len(image_paths)} images individually as fallback")
    
    results = []
    for i, path in enumerate(image_paths):
        logger.info(f"Analyzing image {i+1}/{len(image_paths)}: {os.path.basename(path)}")
        
        # Add delay between requests to avoid rate limiting
        if i > 0:
            time.sleep(config.rate_limit_delay)
        
        analysis = analyze_with_gemini(path, config)
        if analysis:
            results.append({
                'path': path,
                'analysis': analysis,
                'datetime': get_exif_datetime(path)
            })
    
    return results


def analyze_images_gemini_batched(image_paths: List[str], config: Config) -> List[Dict]:
    """Optimized Gemini analysis using existing batch processing"""
    results = []
    
    # Use existing Gemini batch processing (up to 16 images per batch)
    batch_size = 16
    
    if TQDM_AVAILABLE:
        progress_bar = tqdm(total=len(image_paths), desc="Gemini Batch", unit="img")
    
    for i in range(0, len(image_paths), batch_size):
        batch_paths = image_paths[i:i + batch_size]
        
        try:
            batch_results = analyze_batch_with_gemini(batch_paths, config)
            results.extend(batch_results)
            
            if TQDM_AVAILABLE:
                progress_bar.update(len(batch_paths))
                progress_bar.set_postfix({
                    'Batches': f"{(i//batch_size)+1}/{(len(image_paths)-1)//batch_size+1}",
                    'Success': len(results)
                })
                
        except Exception as e:
            logger.error(f"Gemini batch analysis failed: {e}")
            # Fallback to individual analysis for this batch
            for path in batch_paths:
                try:
                    base64_image = encode_image_to_base64(path, config)
                    if base64_image:
                        result = analyze_with_gemini(base64_image, config)
                        if result:
                            results.append({
                                'path': path,
                                'analysis': result,
                                'datetime': get_exif_datetime(path)
                            })
                except Exception as e2:
                    logger.error(f"Individual fallback failed for {os.path.basename(path)}: {e2}")
                
                if TQDM_AVAILABLE:
                    progress_bar.update(1)
    
    if TQDM_AVAILABLE:
        progress_bar.close()
    
    logger.info(f"Gemini batch analysis complete: {len(results)}/{len(image_paths)} successful")
    return results


def analyze_with_gemini(base64_image: str, config: Config) -> Optional[Dict]:
    """Analyze image using Gemini API with advanced Instagram scoring"""
    if not config.gemini["api_key"]:
        logger.error("Gemini API key not provided")
        return None
    
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
    
    url = f"{config.gemini['api_url']}/{config.gemini['model']}:generateContent"
    
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt_text},
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": base64_image
                    }
                }
            ]
        }],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 2048,
        }
    }
    
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": config.gemini["api_key"]
    }
    
    try:
        response_data = make_rate_limited_request(url, payload, headers, config, "individual")
        
        if 'candidates' in response_data and len(response_data['candidates']) > 0:
            content = response_data['candidates'][0]['content']['parts'][0]['text']
            
            # Debug: log the raw response
            logger.debug(f"Raw Gemini response: {content[:200]}...")
            
            # Clean up response
            content = content.strip()
            if content.startswith('```json'):
                content = content[7:]
            if content.endswith('```'):
                content = content[:-3]
            content = content.strip()
            
            try:
                analysis = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse JSON, trying to extract JSON from text: {e}")
                # Try to find JSON in the response
                import re
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    try:
                        analysis = json.loads(json_match.group())
                    except json.JSONDecodeError:
                        logger.error(f"Could not parse extracted JSON: {json_match.group()[:100]}...")
                        return None
                else:
                    logger.error(f"No JSON found in response: {content[:100]}...")
                    return None
            
            # Calculate composite Instagram score
            # Check if we have at least some required fields
            required_fields = ['technical_score', 'visual_appeal', 'engagement_score', 'uniqueness']
            missing_fields = [field for field in required_fields if field not in analysis]
            
            if missing_fields:
                logger.warning(f"Missing fields in Gemini response: {missing_fields}")
                # Fill in missing fields with default values
                for field in missing_fields:
                    analysis[field] = 5.0  # Default middle score
            
            # Enhanced weighted composite score
            weights = {
                'technical_score': 0.15,
                'visual_appeal': 0.25,
                'engagement_score': 0.30,
                'uniqueness': 0.20,
                'story_potential': 0.10
            }
            
            composite_score = (
                analysis.get('technical_score', 5.0) * weights['technical_score'] +
                analysis.get('visual_appeal', 5.0) * weights['visual_appeal'] +
                analysis.get('engagement_score', 5.0) * weights['engagement_score'] +
                analysis.get('uniqueness', 5.0) * weights['uniqueness'] +
                analysis.get('story_potential', 5.0) * weights['story_potential']
            )
            analysis['composite_score'] = round(composite_score, 2)
            
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
            
            analysis['instagram_tier'] = tier
            
            # More selective Instagram worthy determination
            analysis['instagram_worthy'] = tier in ['premium', 'excellent'] or composite_score >= 7.0
            
            return analysis
        else:
            logger.warning("No candidates in Gemini response")
            return None
            
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Gemini JSON response: {e}")
        return None
    except Exception as e:
        logger.error(f"Gemini analysis error: {e}")
        return None


def generate_content_with_gemini(base64_images: List[str], config: Config) -> Optional[Dict]:
    """Generate content using Gemini API"""
    if not config.gemini["api_key"]:
        logger.error("Gemini API key not provided")
        return None
    
    prompt_text = """You are a creative social media manager. Looking at these images, generate a JSON object with these exact keys:
    1. "caption_options": A list of 3 different, engaging Instagram caption ideas
    2. "hashtags": A list of 15-20 relevant hashtags without the # symbol
    3. "post_theme": A brief description of the overall theme
    
    Return ONLY the JSON object, no other text."""
    
    url = f"{config.gemini['api_url']}/{config.gemini['model']}:generateContent"
    
    parts = [{"text": prompt_text}]
    for base64_image in base64_images:
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64_image
            }
        })
    
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2048,
        }
    }
    
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": config.gemini["api_key"]
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=300)
        response.raise_for_status()
        
        response_data = response.json()
        
        if 'candidates' in response_data and len(response_data['candidates']) > 0:
            content = response_data['candidates'][0]['content']['parts'][0]['text']
            
            # Clean up response
            content = content.strip()
            if content.startswith('```json'):
                content = content[7:]
            if content.endswith('```'):
                content = content[:-3]
            content = content.strip()
            
            parsed_content = json.loads(content)
            
            if "caption_options" in parsed_content and "hashtags" in parsed_content:
                return parsed_content
            else:
                return None
        else:
            return None
            
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Gemini content JSON: {e}")
        return None
    except Exception as e:
        logger.error(f"Gemini content generation error: {e}")
        return None
