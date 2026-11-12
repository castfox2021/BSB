import 'reflect-metadata';
import { Controller, Get, Module, ServiceUnavailableException, Logger } from '@nestjs/common';
import { NestFactory } from '@nestjs/core';
import { FastifyAdapter } from '@nestjs/platform-fastify';
import { dependencies, mode } from './health';
@Controller('api/v1/system')
class HealthController {
  @Get('health') async health() {
    try { return { status: 'ok', service: 'api', ...mode(), dependencies: await dependencies() }; }
    catch { throw new ServiceUnavailableException({ status: 'degraded', service: 'api', ...mode() }); }
  }
}
@Module({ controllers: [HealthController] })
class AppModule {}
async function bootstrap() {
  mode();
  if (!process.env.DATABASE_URL || !process.env.REDIS_URL) throw new Error('DATABASE_URL and REDIS_URL are required');
  const app = await NestFactory.create(AppModule, new FastifyAdapter());
  app.enableShutdownHooks();
  await app.listen(3001, '0.0.0.0');
}
bootstrap().catch((error: Error) => { Logger.error(error.message, undefined, 'Bootstrap'); process.exit(1); });
