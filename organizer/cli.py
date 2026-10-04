"""Command-line interface: argument parsing and the main pipeline entrypoint."""
import os
import json
import sys
import time
import argparse
from pathlib import Path

from .common import (
    logger,
    analyze_photo_patterns,
    create_analytics_visualizations,
    generate_posting_schedule,
    auto_enhance_image,
)
from .config import Config
from .analysis import (
    analyze_images_parallel,
    filter_contextually_similar_images,
    generate_analytics_report,
    get_image_files,
    organize_photos,
)
from .images import (
    cleanup_temp_directory,
    convert_and_prepare_images,
    filter_similar_images,
    get_exif_datetime,
)
from .providers.gemini import analyze_batch_with_gemini


def setup_cli_args(argv=None):
    """Setup command line arguments"""
    parser = argparse.ArgumentParser(description="Instagram Photo Organizer with AI Analysis")
    
    # Basic options
    parser.add_argument("--source", "-s", help="Source folder path")
    parser.add_argument("--output", "-o", help="Output folder path")
    parser.add_argument("--config", "-c", default="config.json", help="Config file path")
    
    # Processing options
    parser.add_argument("--dev-mode", "-d", action="store_true", help="Enable development mode")
    parser.add_argument("--limit", "-l", type=int, help="Limit photos in dev mode")
    parser.add_argument("--post-size", "-p", type=int, help="Photos per post")
    parser.add_argument("--similarity", type=int, help="Similarity threshold (0-10)")
    
    # AI Provider options
    parser.add_argument("--ai-provider", choices=['llama', 'ollama', 'gemini'], help="AI provider to use")
    parser.add_argument("--llama-key", help="Llama API key")
    parser.add_argument("--gemini-key", help="Gemini API key")
    parser.add_argument("--ollama-url", help="Ollama API URL")
    parser.add_argument("--ollama-model", help="Ollama model name")
    
    # Mode selection
    parser.add_argument("--simple-mode", action="store_true", help="Run in simple mode (core features only)")
    
    # Performance options
    parser.add_argument("--parallel-workers", type=int, help="Number of parallel AI analysis workers")
    parser.add_argument("--batch-size", type=int, help="Batch size for AI analysis")
    parser.add_argument("--fast-mode", action="store_true", help="Enable fast processing mode")
    parser.add_argument("--no-cache", action="store_true", help="Disable AI analysis caching")
    
    # Contextual filtering options
    parser.add_argument("--no-contextual-filter", action="store_true", help="Disable AI-based contextual similarity filtering")
    parser.add_argument("--contextual-threshold", type=float, help="Contextual similarity threshold (0.0-1.0)")

    # Trip-dump pipeline subcommand: delegates to the trip_dumps/ scripts,
    # forwarding everything after the step verbatim.
    sub = parser.add_subparsers(dest="subcommand", metavar="command")
    td = sub.add_parser("trip-dump",
                        help="trip-dump pipeline: cluster photos into trips, curate carousel drafts")
    td.add_argument("step", choices=["cluster", "curate"],
                    help="cluster -> trip_dumps/cluster_trips.py; "
                         "curate -> trip_dumps/curate_carousel.py")
    td.add_argument("step_args", nargs=argparse.REMAINDER,
                    help="arguments passed through to the trip-dump script "
                         "(e.g. 'trip-dump cluster --help' shows that script's flags)")

    return parser.parse_args(argv)


def _run_trip_dump(step, step_args):
    """Run a trip-dump pipeline step, forwarding argv to the trip_dumps script."""
    from trip_dumps.cluster_trips import main as cluster_main
    from trip_dumps.curate_carousel import main as curate_main
    if step == "cluster":
        sys.exit(cluster_main(list(step_args)))
    sys.exit(curate_main(list(step_args)))


def main(argv=None):
    """Main function"""
    try:
        # Parse arguments and setup config
        args = setup_cli_args(argv)

        # Trip-dump pipeline subcommand: delegate to trip_dumps/ scripts.
        if getattr(args, "subcommand", None) == "trip-dump":
            _run_trip_dump(args.step, args.step_args)
            return

        config = Config(args.config)
        config.update_from_args(args)
        
        logger.info("Starting Instagram photo organizer...")
        logger.info(f"Mode: {'Simple' if args.simple_mode else 'Advanced'}")
        logger.info(f"AI Provider: {config.ai_provider}")
        logger.info(f"Source folder: {config.source_folder}")
        logger.info(f"Output folder: {config.output_folder}")
        logger.info(f"Development mode: {'ON' if not config.process_all else 'OFF'}")
        
        # Validate setup
        if config.ai_provider == 'gemini' and not config.gemini["api_key"]:
            logger.error("Gemini API key required. Use --gemini-key or set in config file.")
            return
        
        # Get image files
        all_source_paths = get_image_files(config.source_folder)
        if not all_source_paths:
            logger.error("No supported image files found")
            return
        
        # Filter similar images
        unique_source_paths = filter_similar_images(all_source_paths, config.similarity_threshold, config)
        
        # Convert and prepare images (optimized - no unnecessary conversions)
        processing_folder, image_paths_in_temp = convert_and_prepare_images(
            config.source_folder, 
            config.temp_convert_folder, 
            unique_source_paths,
            config.image_quality
        )
        
        # Enhance images if enabled
        if config.enable_enhancement:
            logger.info("Enhancing images...")
            enhanced_dir = os.path.join(config.temp_convert_folder, "enhanced")
            Path(enhanced_dir).mkdir(exist_ok=True)
            
            enhanced_paths = []
            for path in image_paths_in_temp:
                enhanced_path = os.path.join(enhanced_dir, os.path.basename(path))
                if auto_enhance_image(path, enhanced_path):
                    enhanced_paths.append(enhanced_path)
                else:
                    enhanced_paths.append(path)
            
            image_paths_in_temp = enhanced_paths
        
        # Get EXIF data and sort by date
        all_image_data = []
        for path in image_paths_in_temp:
            image_data = {
                'path': path,
                'datetime': get_exif_datetime(path)
            }
            all_image_data.append(image_data)
        
        all_image_data.sort(key=lambda x: x['datetime'])
        
        # Apply dev mode limit if needed
        if not config.process_all:
            limit = min(config.dev_mode_limit, len(all_image_data))
            logger.info(f"Development mode: Processing {limit} photos")
            image_data_to_process = all_image_data[:limit]
        else:
            image_data_to_process = all_image_data
        
        if not image_data_to_process:
            logger.error("No processable images found")
            return
        
        # Analyze images with AI using parallel processing
        image_paths = [data['path'] for data in image_data_to_process]
        
        # Choose analysis method based on configuration and provider
        if config.ai_provider == 'gemini' and config.ai_batch_size > 1 and len(image_paths) > config.ai_batch_size:
            logger.info(f"Using batch analysis with Gemini (batch size: {config.ai_batch_size})")
            final_analyzed_data = []
            
            # Process in batches
            total_batches = (len(image_paths) + config.ai_batch_size - 1) // config.ai_batch_size
            
            for i in range(0, len(image_paths), config.ai_batch_size):
                batch_paths = image_paths[i:i + config.ai_batch_size]
                batch_num = i // config.ai_batch_size + 1
                
                batch_results = analyze_batch_with_gemini(batch_paths, config)
                final_analyzed_data.extend(batch_results)
                
                logger.info(f"Completed batch {batch_num}/{total_batches}")
                
                # Add delay between batches to respect rate limits (except for last batch)
                if batch_num < total_batches:
                    delay = 2.0  # 2 second delay between batches
                    logger.debug(f"Waiting {delay}s before next batch...")
                    time.sleep(delay)
        else:
            # Use parallel individual analysis
            final_analyzed_data = analyze_images_parallel(image_paths, config)
        
        # Progress bar is handled within the analysis functions
        
        # Generate analytics if enabled
        if config.enable_analytics and final_analyzed_data:
            logger.info("Generating analytics...")
            analytics = analyze_photo_patterns(final_analyzed_data)
            
            analytics_dir = os.path.join(config.output_folder, "analytics")
            Path(analytics_dir).mkdir(parents=True, exist_ok=True)
            
            generate_analytics_report(analytics, os.path.join(analytics_dir, "report.txt"))
            create_analytics_visualizations(analytics, analytics_dir)
        
        # Apply contextual filtering after AI analysis
        if final_analyzed_data:
            logger.info(f"Successfully analyzed {len(final_analyzed_data)} images")
            
            # Filter contextually similar images using AI analysis
            if config.enable_contextual_filtering:
                final_analyzed_data = filter_contextually_similar_images(final_analyzed_data, config)
                logger.info(f"After contextual filtering: {len(final_analyzed_data)} unique context images")
            
            organize_photos(final_analyzed_data, config)
            
            # Generate posting schedule if enabled
            if config.enable_scheduling:
                logger.info("Generating posting schedule...")
                worthy_count = len([d for d in final_analyzed_data if d['analysis'].get('instagram_worthy', False)])
                num_posts = worthy_count // config.post_size
                
                if num_posts > 0:
                    schedule = generate_posting_schedule(num_posts)
                    schedule_path = os.path.join(config.output_folder, "posting_schedule.json")
                    
                    with open(schedule_path, 'w') as f:
                        json.dump(schedule, f, indent=2, default=str)
                    
                    logger.info(f"Saved posting schedule to {schedule_path}")
            
            logger.info("✅ Instagram photo organization complete!")
        else:
            logger.error(f"Could not analyze any photos. Check your {config.ai_provider} setup.")
        
        # Clean up temporary directory
        cleanup_temp_directory(processing_folder, keep_files=config.keep_temp_files)
            
    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
        # Clean up on interrupt
        if 'processing_folder' in locals():
            cleanup_temp_directory(processing_folder, keep_files=config.keep_temp_files)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        # Clean up on error
        if 'processing_folder' in locals():
            cleanup_temp_directory(processing_folder, keep_files=config.keep_temp_files)
        raise
