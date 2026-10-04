"""AI Instagram Photo Organizer - modular package.

Pure move-refactor of the former ``ai_instagram_organizer.py`` god-file into
coherent modules. Every public name keeps its original signature and behavior;
the legacy ``ai_instagram_organizer`` module remains as a thin shim that
re-exports everything defined here.

Layout:
    organizer.common ......... logging, optional deps, constants, caches
    organizer.config ......... Config class and config helpers
    organizer.ratelimit ...... unified RateLimiter + provider subclasses
    organizer.images ......... EXIF/date helpers, hashing, conversion, prefiltering
    organizer.providers.gemini  Gemini analysis / content generation
    organizer.providers.llama   Llama analysis / content generation
    organizer.providers.ollama  Ollama analysis / content generation
    organizer.analysis ....... filtering, scoring, selection, orchestration
    organizer.cli ............ setup_cli_args() + main()
"""

from .common import (
    logger,
    tqdm,
    TQDM_AVAILABLE,
    ADVANCED_FEATURES,
    SUPPORTED_FORMATS,
    CONVERTED_FORMATS,
    THUMBNAIL_SIZE,
    analyze_photo_patterns,
    create_analytics_visualizations,
    generate_posting_schedule,
    auto_enhance_image,
    HashtagOptimizer,
    create_platform_variants,
)
from .config import Config
from .ratelimit import RateLimiter, GeminiRateLimiter, LlamaRateLimiter
from .images import (
    get_image_hash_for_cache,
    quick_quality_filter,
    get_exif_datetime,
    fast_prefilter_images,
    compute_image_hash,
    filter_similar_images,
    convert_and_prepare_images,
    cleanup_temp_directory,
    encode_image_to_base64,
)
from .providers.gemini import (
    analyze_images_gemini_optimized,
    analyze_single_image_gemini_with_limiter,
    analyze_single_image_gemini_direct,
    make_rate_limited_request,
    analyze_batch_with_gemini,
    analyze_images_individually_fallback,
    analyze_images_gemini_batched,
    analyze_with_gemini,
    generate_content_with_gemini,
)
from .providers.llama import (
    analyze_images_llama_optimized,
    analyze_single_image_llama,
    analyze_with_llama,
    analyze_batch_with_llama,
    generate_content_with_llama,
)
from .providers.ollama import (
    analyze_with_ollama,
    generate_content_with_ollama,
)
from .analysis import (
    filter_contextually_similar_images,
    calculate_contextual_similarity,
    calculate_text_similarity,
    select_best_from_context_group,
    analyze_image_with_ai,
    analyze_images_parallel,
    analyze_images_multi_provider,
    analyze_images_with_load_balancing,
    generate_post_content,
    save_post_content,
    organize_photos_enhanced,
    determine_setting,
    determine_time_of_day,
    determine_people_count,
    create_premium_posts,
    create_diverse_posts,
    select_diverse_photo_set,
    calculate_diversity_score,
    create_theme_posts,
    create_chronological_posts,
    get_remaining_photos,
    save_post_collection,
    generate_analytics_report,
    organize_photos,
    get_image_files,
)
from .cli import setup_cli_args, main

__all__ = [
    # common
    "logger",
    "tqdm",
    "TQDM_AVAILABLE",
    "ADVANCED_FEATURES",
    "SUPPORTED_FORMATS",
    "CONVERTED_FORMATS",
    "THUMBNAIL_SIZE",
    "analyze_photo_patterns",
    "create_analytics_visualizations",
    "generate_posting_schedule",
    "auto_enhance_image",
    "HashtagOptimizer",
    "create_platform_variants",
    # config
    "Config",
    # ratelimit
    "RateLimiter",
    "GeminiRateLimiter",
    "LlamaRateLimiter",
    # images
    "get_image_hash_for_cache",
    "quick_quality_filter",
    "get_exif_datetime",
    "fast_prefilter_images",
    "compute_image_hash",
    "filter_similar_images",
    "convert_and_prepare_images",
    "cleanup_temp_directory",
    "encode_image_to_base64",
    # providers.gemini
    "analyze_images_gemini_optimized",
    "analyze_single_image_gemini_with_limiter",
    "analyze_single_image_gemini_direct",
    "make_rate_limited_request",
    "analyze_batch_with_gemini",
    "analyze_images_individually_fallback",
    "analyze_images_gemini_batched",
    "analyze_with_gemini",
    "generate_content_with_gemini",
    # providers.llama
    "analyze_images_llama_optimized",
    "analyze_single_image_llama",
    "analyze_with_llama",
    "analyze_batch_with_llama",
    "generate_content_with_llama",
    # providers.ollama
    "analyze_with_ollama",
    "generate_content_with_ollama",
    # analysis
    "filter_contextually_similar_images",
    "calculate_contextual_similarity",
    "calculate_text_similarity",
    "select_best_from_context_group",
    "analyze_image_with_ai",
    "analyze_images_parallel",
    "analyze_images_multi_provider",
    "analyze_images_with_load_balancing",
    "generate_post_content",
    "save_post_content",
    "organize_photos_enhanced",
    "determine_setting",
    "determine_time_of_day",
    "determine_people_count",
    "create_premium_posts",
    "create_diverse_posts",
    "select_diverse_photo_set",
    "calculate_diversity_score",
    "create_theme_posts",
    "create_chronological_posts",
    "get_remaining_photos",
    "save_post_collection",
    "generate_analytics_report",
    "organize_photos",
    "get_image_files",
    # cli
    "setup_cli_args",
    "main",
]
