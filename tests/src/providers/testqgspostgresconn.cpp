/***************************************************************************
    testqgspostgresconn.cpp
    ---------------------
    begin                : August 2016
    copyright            : (C) 2016 by Patrick Valsecchi
    email                : patrick dot valsecchi at camptocamp dot com
 ***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
#include "qgsconfig.h"

#include "qgsapplication.h"
#include "qgsdatasourceuri.h"
#include "qgsdbquerylog.h"
#include "qgsfields.h"
#include "qgspostgresconn.h"
#include "qgspostgresprovider.h"
#include "qgspostgresutils.h"
#include "qgssettings.h"
#include "qgstest.h"

#include <QObject>
#include <QSignalSpy>
#include <QString>

using namespace Qt::StringLiterals;

// Helper function for QCOMPARE
char *toString( const QgsPostgresGeometryColumnType &t )
{
  const char *ptr;
  switch ( t )
  {
    case SctNone:
      ptr = "None";
      break;
    case SctGeometry:
      ptr = "Geometry";
      break;
    case SctGeography:
      ptr = "Geography";
      break;
    case SctTopoGeometry:
      ptr = "TopoGeometry";
      break;
    case SctPcPatch:
      ptr = "PcPatch";
      break;
    case SctRaster:
      ptr = "Raster";
      break;
    default:
      ptr = "Unknown";
      break;
  }
  char *dst = new char[16];
  return qstrcpy( dst, ptr );
}

// Helper function for QCOMPARE
char *toString( const ExecStatusType &status )
{
  return qstrdup( ::PQresStatus( status ) );
}

class TestQgsPostgresConn : public QObject
{
    Q_OBJECT

  private:
#ifdef ENABLE_PGTEST
    QgsPostgresConn *_connection;


    QgsPostgresConn *getConnection()
    {
      if ( !_connection )
      {
        const char *connstring = getenv( "QGIS_PGTEST_DB" );
        if ( !connstring )
          connstring = "service=qgis_test";
        _connection = QgsPostgresConn::connectDb( connstring, true );
      }
      return _connection;
    }

    QgsPostgresConn *_batchConnection = nullptr;

    /**
     * Returns a read-write connection for the PQexecPreparedBatch tests, with an empty temporary table and
     * a prepared "batch_insert" statement, and sets whether batches run pipelined or sequentially.
     */
    QgsPostgresConn *getBatchConnection( bool disablePipelining )
    {
      QgsSettings().setValue( u"PostgreSQL/disablePipelining"_s, disablePipelining, QgsSettings::Providers );

      if ( !_batchConnection )
      {
        const char *connstring = getenv( "QGIS_PGTEST_DB" );
        if ( !connstring )
          connstring = "service=qgis_test";
        _batchConnection = QgsPostgresConn::connectDb( connstring, false, false );
        if ( !_batchConnection )
          return nullptr;

        if ( !_batchConnection->PQexecNR( u"CREATE TEMP TABLE batch_test ( id int PRIMARY KEY, txt text )"_s ) )
          return nullptr;
        QgsPostgresResult stmt( _batchConnection->PQprepare( u"batch_insert"_s, u"INSERT INTO batch_test ( id, txt ) VALUES ( $1, $2 ) RETURNING id, txt"_s, 2, nullptr ) );
        if ( stmt.PQresultStatus() != PGRES_COMMAND_OK )
          return nullptr;
      }

      if ( !_batchConnection->PQexecNR( u"TRUNCATE batch_test"_s ) )
        return nullptr;
      return _batchConnection;
    }

    //! Runs a batch of batch_insert, and returns its query log entry in \a logEntry
    std::vector<std::unique_ptr<QgsPostgresResult>> execBatch( QgsPostgresConn *conn, const QList<QStringList> &paramsList, QgsDatabaseQueryLogEntry &logEntry )
    {
      QgsDatabaseQueryLog::setEnabled( true );
      QSignalSpy spy( QgsApplication::databaseQueryLog(), &QgsDatabaseQueryLog::queryFinished );

      std::vector<std::unique_ptr<QgsPostgresResult>> results = conn->PQexecPreparedBatch( u"batch_insert"_s, paramsList );

      QCoreApplication::processEvents();
      QgsDatabaseQueryLog::setEnabled( false );
      for ( const QList<QVariant> &args : std::as_const( spy ) )
      {
        const QgsDatabaseQueryLogEntry entry = args.at( 0 ).value<QgsDatabaseQueryLogEntry>();
        if ( entry.query.startsWith( "PQexecPreparedBatch(batch_insert)"_L1 ) )
          logEntry = entry;
      }
      return results;
    }

    //! Checks that the connection is out of pipeline mode and runs queries without a reset
    void verifyConnectionUsable( QgsPostgresConn *conn )
    {
#ifdef LIBPQ_HAS_PIPELINING
      QVERIFY( ::PQpipelineStatus( conn->pgConnection() ) == PQ_PIPELINE_OFF );
#endif
      QgsPostgresResult result( conn->PQexec( u"SELECT 1"_s, true, false ) );
      QCOMPARE( result.PQresultStatus(), PGRES_TUPLES_OK );
    }

    int batchTestRowCount( QgsPostgresConn *conn )
    {
      QgsPostgresResult result( conn->PQexec( u"SELECT count(*) FROM batch_test"_s ) );
      return result.PQgetvalue( 0, 0 ).toInt();
    }

    static void addPipeliningRows()
    {
      QTest::addColumn<bool>( "disablePipelining" );
      QTest::addColumn<QString>( "logSuffix" );

#ifdef LIBPQ_HAS_PIPELINING
      QTest::newRow( "pipeline" ) << false << u"[pipeline]"_s;
#else
      QTest::newRow( "no pipelining in libpq" ) << false << u"[sequential]"_s;
#endif
      QTest::newRow( "pipelining disabled" ) << true << u"[sequential]"_s;
    }
#endif

  private slots:
    void initTestCase() // will be called before the first testfunction is executed.
    {
#ifdef ENABLE_PGTEST
      this->_connection = nullptr;
#endif
    }
    void cleanupTestCase() // will be called after the last testfunction was executed.
    {
#ifdef ENABLE_PGTEST
      if ( this->_connection )
        this->_connection->unref();
      if ( _batchConnection )
        _batchConnection->unref();
      QgsSettings().remove( u"PostgreSQL/disablePipelining"_s, QgsSettings::Providers );
#endif
    }

    void quotedValueHstore()
    {
      QVariantMap map;
      map[u"1"_s] = "2";
      map[u"a"_s] = "b \"c' \\x";

      const QString actual = QgsPostgresConn::quotedValue( map );
      QCOMPARE( actual, QString( "E'\"1\"=>\"2\",\"a\"=>\"b \\\\\"c\\' \\\\\\\\x\"'::hstore" ) );
    }

    void quotedValueString()
    {
      QCOMPARE( QgsPostgresConn::quotedValue( "b" ), QString( "'b'" ) );
      QCOMPARE( QgsPostgresConn::quotedValue( "b's" ), QString( "'b''s'" ) );
      QCOMPARE( QgsPostgresConn::quotedValue( "b \"c' \\x" ), QString( "E'b \"c'' \\\\x'" ) );
    }

    void quotedValueDatetime()
    {
      QCOMPARE( QgsPostgresConn::quotedValue( QDateTime::fromString( "2020-05-07 17:56:00", "yyyy-MM-dd HH:mm:ss" ) ), QString( "'2020-05-07T17:56:00.000'" ) );

      QgsFields fields;
      QgsField f;
      f.setName( "ts" );
      f.setType( QMetaType::Type::QDateTime );
      f.setTypeName( "timestamp" );
      fields.append( f );
      QgsField f2;
      f2.setName( "pk" );
      f2.setType( QMetaType::Type::LongLong );
      f2.setTypeName( "serial8" );
      fields.append( f2 );

      QList<int> pkAttrs;
      pkAttrs.append( 0 );
      pkAttrs.append( 1 );

      QgsPostgresSharedData *shared = new QgsPostgresSharedData;
      QVariantList qvlist;
      qvlist.append( QDateTime::fromString( "2020-05-07 17:56:00", "yyyy-MM-dd HH:mm:ss" ) );
      qvlist.append( 123LL );
      shared->insertFid( 1LL, qvlist );

      QCOMPARE( QgsPostgresUtils::whereClause( 1LL, fields, nullptr, QgsPostgresPrimaryKeyType::PktFidMap, pkAttrs, std::shared_ptr<QgsPostgresSharedData>( shared ) ), QString( "\"ts\"='2020-05-07T17:56:00.000' AND \"pk\"=123" ) );
    }

    void quotedValueStringArray()
    {
      QStringList list;
      list << u"a"_s << u"b \"c' \\x"_s;
      const QString actual = QgsPostgresConn::quotedValue( list );
      QCOMPARE( actual, QString( "E'{\"a\",\"b \\\\\"c\\' \\\\\\\\x\"}'" ) );
    }

    void quotedValueIntArray()
    {
      QVariantList list;
      list << 1 << -5;
      const QString actual = QgsPostgresConn::quotedValue( list );
      QCOMPARE( actual, QString( "E'{1,-5}'" ) );
    }

    void quotedValue2DimArray()
    {
      QStringList list;
      list << u"{\"hello foo\",b}"_s << u"{c,\"hello bar\"}"_s;
      const QString actual = QgsPostgresConn::quotedValue( list );
      QCOMPARE( actual, QString( "E'{{\"hello foo\",b},{c,\"hello bar\"}}'" ) );
    }

#ifdef ENABLE_PGTEST
    void supportedLayers()
    {
      QGSTEST_NEED_PGTEST_DB();

      QgsPostgresConn *conn = getConnection();
      QVERIFY( conn != nullptr );
      QVector<QgsPostgresLayerProperty> layers;
      QMap<QString, QgsPostgresLayerProperty> layersMap;

      const bool success = conn->supportedLayers(
        layers,
        false,      // searchGeometryColumnsOnly
        false,      // allowGeometrylessTables
        false,      // allowRasterOverviewTables
        "qgis_test" // schema
      );
      QVERIFY( success );

      // Test no duplicates are reported by supportedLayers
      for ( const QgsPostgresLayerProperty &l : std::as_const( layers ) )
      {
        const QString key = QString( "%1.%2.%3" ).arg( l.schemaName, l.tableName, l.geometryColName );
        const auto i = layersMap.find( key );
        if ( i != layersMap.end() )
        {
          QFAIL( QString( "Layer %1 returned multiple times by supportedLayers" ).arg( key ).toUtf8().data() );
        }
        layersMap.insert( key, l );
        QCOMPARE( l.schemaName, u"qgis_test"_s );
      }

      // Test qgis_test.TopoLayer1.topogeom
      const QString key = QString( "qgis_test.TopoLayer1.topogeom" );
      const auto lit = layersMap.find( key );
      QVERIFY2( lit != layersMap.end(), "Layer qgis_test.TopoLayer1.topogeom not returned by supportedLayers" );
      QCOMPARE( lit->geometryColName, "topogeom" );
      QCOMPARE( lit->geometryColType, SctTopoGeometry );
      // TODO: add more tests
    }

    void testGetSchemas()
    {
      QGSTEST_NEED_PGTEST_DB();

      QgsPostgresConn *conn = getConnection();
      QVERIFY( conn );

      QList<QgsPostgresSchemaProperty> schemas;
      conn->getSchemas( schemas );

      QList<QgsPostgresSchemaProperty> filteredSchemas;
      std::copy_if( schemas.constBegin(), schemas.constEnd(), std::back_inserter( filteredSchemas ), []( const QgsPostgresSchemaProperty &schema ) { return schema.name == "qgis_test"_L1; } );
      QCOMPARE( filteredSchemas.size(), 1 );
      filteredSchemas.clear();
      std::copy_if( schemas.constBegin(), schemas.constEnd(), std::back_inserter( filteredSchemas ), []( const QgsPostgresSchemaProperty &schema ) { return schema.name == "public"_L1; } );
      QCOMPARE( filteredSchemas.size(), 1 );

      // restrict to matching schemas
      schemas.clear();
      conn->getSchemas( schemas, { u"qgis_test"_s } );
      QCOMPARE( schemas.size(), 1 );
      QCOMPARE( schemas.at( 0 ).name, u"qgis_test"_s );
    }

    void connectDb()
    {
      QGSTEST_NEED_PGTEST_DB();

      QgsPostgresConn *conn = getConnection();
      QVERIFY( conn != nullptr );

      const QString sql = u"SELECT SESSION_USER, CURRENT_USER;"_s;

      QgsPostgresResult result( conn->PQexec( sql ) );
      // current_user is the same as session_user
      QCOMPARE( result.PQgetvalue( 0, 0 ), result.PQgetvalue( 0, 1 ) );

      const char *connstring = getenv( "QGIS_PGTEST_DB" );
      if ( !connstring )
        connstring = "service=qgis_test";
      const QString conninfo( connstring );
      QgsDataSourceUri uri( conninfo );

      // Update postgres uri to use qgis_test_user which is member of qgis_test_group
      uri.setUsername( u"qgis_test_user"_s );
      uri.setPassword( u"qgis_test_user_password"_s );

      // Connection with qgis_test_user without session_role
      conn = QgsPostgresConn::connectDb( uri, true );
      QVERIFY( conn );
      result = conn->PQexec( sql );
      const QString session_user = result.PQgetvalue( 0, 0 );
      QCOMPARE( session_user, "qgis_test_user" );
      // current_user is the same as session_user
      QCOMPARE( result.PQgetvalue( 0, 1 ), session_user );
      conn->unref();

      // Add known session_role parameter to postgres uri
      uri.setParam( u"session_role"_s, u"qgis_test_group"_s );
      conn = QgsPostgresConn::connectDb( uri, true );
      QVERIFY( conn );
      result = conn->PQexec( sql );
      // session_user does not change
      QCOMPARE( result.PQgetvalue( 0, 0 ), session_user );
      // current_user has changed
      QCOMPARE( result.PQgetvalue( 0, 1 ), "qgis_test_group" );
      conn->unref();

      // Add unknown session_role parameter to postgres uri
      // uri.setParam( u"session_role"_s,  u"qgis_test_unknown_group"_s );
      // conn = QgsPostgresConn::connectDb( uri.connectionInfo( true ), true );
      // QVERIFY( !conn );
      // conn->unref();

      // Remove session_role parameter from postgre uri
      uri.removeParam( u"session_role"_s );
      conn = QgsPostgresConn::connectDb( uri, true );
      QVERIFY( conn );
      result = conn->PQexec( sql );
      // session_user does not change
      QCOMPARE( result.PQgetvalue( 0, 0 ), session_user );
      // current_user back to session_user
      QCOMPARE( result.PQgetvalue( 0, 1 ), session_user );
      conn->unref();
    }

    void execPreparedBatch_data() { addPipeliningRows(); }

    void execPreparedBatch()
    {
      QGSTEST_NEED_PGTEST_DB();
      QFETCH( bool, disablePipelining );
      QFETCH( QString, logSuffix );

      QgsPostgresConn *conn = getBatchConnection( disablePipelining );
      QVERIFY( conn );

      QList<QStringList> paramsList;
      for ( int i = 0; i < 1000; ++i )
        paramsList << QStringList { QString::number( i ), i % 10 == 0 ? QString() : u"value %1"_s.arg( i ) };

      QgsDatabaseQueryLogEntry logEntry;
      const std::vector<std::unique_ptr<QgsPostgresResult>> results = execBatch( conn, paramsList, logEntry );

      QCOMPARE( results.size(), 1000 );
      for ( int i = 0; i < 1000; ++i )
      {
        QgsPostgresResult &result = *results[i];
        QCOMPARE( result.PQresultStatus(), PGRES_TUPLES_OK );
        QCOMPARE( result.PQgetvalue( 0, 0 ), QString::number( i ) );
        QCOMPARE( result.PQgetisnull( 0, 1 ), i % 10 == 0 );
        if ( i % 10 != 0 )
          QCOMPARE( result.PQgetvalue( 0, 1 ), u"value %1"_s.arg( i ) );
      }

      QCOMPARE( logEntry.query, u"PQexecPreparedBatch(batch_insert) x 1000 %1"_s.arg( logSuffix ) );
      QVERIFY( logEntry.error.isEmpty() );

      verifyConnectionUsable( conn );
      QCOMPARE( batchTestRowCount( conn ), 1000 );
    }

    void execPreparedBatchServerError_data() { addPipeliningRows(); }

    void execPreparedBatchServerError()
    {
      QGSTEST_NEED_PGTEST_DB();
      QFETCH( bool, disablePipelining );
      QFETCH( QString, logSuffix );

      QgsPostgresConn *conn = getBatchConnection( disablePipelining );
      QVERIFY( conn );

      // id 0 again at index 700
      QList<QStringList> paramsList;
      for ( int i = 0; i < 1000; ++i )
        paramsList << QStringList { QString::number( i == 700 ? 0 : i ), u"value"_s };

      // in a transaction, like addFeatures, so both paths leave the same state
      QVERIFY( conn->begin() );
      QgsDatabaseQueryLogEntry logEntry;
      const std::vector<std::unique_ptr<QgsPostgresResult>> results = execBatch( conn, paramsList, logEntry );

      QCOMPARE( results.size(), 701 );
      for ( int i = 0; i < 700; ++i )
        QCOMPARE( results[i]->PQresultStatus(), PGRES_TUPLES_OK );
      QCOMPARE( results.back()->PQresultStatus(), PGRES_FATAL_ERROR );
      QVERIFY2( results.back()->PQresultErrorMessage().contains( "duplicate key value violates unique constraint"_L1 ), results.back()->PQresultErrorMessage().toUtf8().constData() );

      QCOMPARE( logEntry.query, u"PQexecPreparedBatch(batch_insert) x 1000 %1"_s.arg( logSuffix ) );
      QVERIFY( logEntry.error.startsWith( "Statement 701 of 1000 failed: ERROR:  duplicate key value"_L1 ) );

      // the batch ends in the failed transaction, and the rollback works
      QVERIFY( ::PQtransactionStatus( conn->pgConnection() ) == PQTRANS_INERROR );
      QVERIFY( conn->rollback() );
      verifyConnectionUsable( conn );
      QCOMPARE( batchTestRowCount( conn ), 0 );
    }

    void execPreparedBatchSendError_data() { addPipeliningRows(); }

    void execPreparedBatchSendError()
    {
      QGSTEST_NEED_PGTEST_DB();
      QFETCH( bool, disablePipelining );
      QFETCH( QString, logSuffix );

      QgsPostgresConn *conn = getBatchConnection( disablePipelining );
      QVERIFY( conn );

      // libpq refuses to send the entry at index 5, it has too many parameters
      QList<QStringList> paramsList;
      for ( int i = 0; i < 10; ++i )
        paramsList << QStringList { QString::number( i ), u"value"_s };
      paramsList[5] = QStringList();
      for ( int i = 0; i < 65536; ++i )
        paramsList[5] << QString::number( i );

      QgsDatabaseQueryLogEntry logEntry;
      const std::vector<std::unique_ptr<QgsPostgresResult>> results = execBatch( conn, paramsList, logEntry );

      QCOMPARE( results.size(), 6 );
      for ( int i = 0; i < 5; ++i )
        QCOMPARE( results[i]->PQresultStatus(), PGRES_TUPLES_OK );
      QCOMPARE( results.back()->PQresultStatus(), PGRES_FATAL_ERROR );
      QVERIFY2( results.back()->PQresultErrorMessage().contains( "number of parameters must be between 0 and 65535"_L1 ), results.back()->PQresultErrorMessage().toUtf8().constData() );

      QCOMPARE( logEntry.query, u"PQexecPreparedBatch(batch_insert) x 10 %1"_s.arg( logSuffix ) );
      QVERIFY( logEntry.error.startsWith( "Statement 6 of 10 failed: "_L1 ) );

      verifyConnectionUsable( conn );
      // outside a transaction, the statements before the failing one are committed on both paths
      QCOMPARE( batchTestRowCount( conn ), 5 );
    }
#endif
};

QGSTEST_MAIN( TestQgsPostgresConn )
#include "testqgspostgresconn.moc"
